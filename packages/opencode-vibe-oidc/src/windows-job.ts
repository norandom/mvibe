/** Stdlib-only Windows supervisor. See Microsoft's Job Objects, UpdateProcThreadAttribute
 * (PROC_THREAD_ATTRIBUTE_JOB_LIST) and JOBOBJECT_BASIC_ACCOUNTING_INFORMATION docs.
 * Kept in emitted JS so package builds require no external asset-copy step.
 */
export const WINDOWS_JOB_SOURCE = String.raw`"""Private Windows job supervisor; stdout belongs exclusively to the helper."""
import ctypes as c
from ctypes import wintypes as w
import os
import subprocess
import shutil
import sys
import time

k = c.WinDLL("kernel32", use_last_error=True)
SIZE = c.c_size_t
HANDLE = w.HANDLE
class IO_COUNTERS(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
class BASIC_LIMIT(c.Structure):
    _fields_ = [("PerProcessUserTimeLimit", c.c_longlong), ("PerJobUserTimeLimit", c.c_longlong), ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", SIZE), ("MaximumWorkingSetSize", SIZE), ("ActiveProcessLimit", w.DWORD), ("Affinity", SIZE), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
class EXTENDED_LIMIT(c.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", SIZE), ("JobMemoryLimit", SIZE), ("PeakProcessMemoryUsed", SIZE), ("PeakJobMemoryUsed", SIZE)]
class ACCOUNTING(c.Structure):
    _fields_ = [(name, c.c_longlong) for name in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [(name, w.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]
class STARTUP(c.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", c.POINTER(w.BYTE)), ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE)]
class STARTUP_EX(c.Structure):
    _fields_ = [("StartupInfo", STARTUP), ("lpAttributeList", c.c_void_p)]
class PROCESS(c.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]
def api(name, result, *args):
    fn = getattr(k, name)
    fn.restype, fn.argtypes = result, args
    return fn
create_job = api("CreateJobObjectW", HANDLE, c.c_void_p, w.LPCWSTR)
open_job = api("OpenJobObjectW", HANDLE, w.DWORD, w.BOOL, w.LPCWSTR)
set_job = api("SetInformationJobObject", w.BOOL, HANDLE, c.c_int, c.c_void_p, w.DWORD)
query_job = api("QueryInformationJobObject", w.BOOL, HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p)
terminate_job = api("TerminateJobObject", w.BOOL, HANDLE, w.UINT)
close = api("CloseHandle", w.BOOL, HANDLE)
initialize = api("InitializeProcThreadAttributeList", w.BOOL, c.c_void_p, w.DWORD, w.DWORD, c.POINTER(SIZE))
update = api("UpdateProcThreadAttribute", w.BOOL, c.c_void_p, w.DWORD, SIZE, c.c_void_p, SIZE, c.c_void_p, c.c_void_p)
delete = api("DeleteProcThreadAttributeList", None, c.c_void_p)
create_process = api("CreateProcessW", w.BOOL, w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(STARTUP_EX), c.POINTER(PROCESS))
resume = api("ResumeThread", w.DWORD, HANDLE)
wait = api("WaitForSingleObject", w.DWORD, HANDLE, w.DWORD)
get_exit = api("GetExitCodeProcess", w.BOOL, HANDLE, c.POINTER(w.DWORD))
get_std = api("GetStdHandle", HANDLE, w.DWORD)
def check(value):
    if not value:
        raise OSError("Windows helper ownership operation failed")
    return value
def drain(job):
    check(terminate_job(job, 1))
    deadline = time.monotonic() + 3
    while True:
        info = ACCOUNTING()
        check(query_job(job, 1, c.byref(info), c.sizeof(info), None))
        if info.ActiveProcesses == 0:
            return
        if time.monotonic() >= deadline:
            raise OSError("Windows helper ownership drain failed")
        time.sleep(0.01)
def main():
    mode, name = sys.argv[1:3]
    if mode == "stop":
        # JOB_OBJECT_TERMINATE | JOB_OBJECT_QUERY. Missing means no associated
        # processes remain: Windows destroys the job after handles/processes close.
        job = open_job(0x0008 | 0x0004, False, name)
        if not job:
            if c.get_last_error() == 2:
                return 0
            raise OSError("Windows helper ownership could not be opened")
        try:
            drain(job)
        finally:
            close(job)
        return 0
    job = check(create_job(None, name))
    if c.get_last_error() == 183:
        close(job)
        raise OSError("Windows helper ownership name collision")
    attributes = None
    process = PROCESS()
    try:
        limits = EXTENDED_LIMIT()
        # No breakaway flags: every descendant remains owned. The handle is
        # non-inheritable, so abrupt supervisor death also closes the last handle.
        limits.BasicLimitInformation.LimitFlags = 0x2000
        check(set_job(job, 9, c.byref(limits), c.sizeof(limits)))
        size = SIZE()
        initialize(None, 1, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        check(initialize(attributes, 1, 0, c.byref(size)))
        jobs = (HANDLE * 1)(job)
        # PROC_THREAD_ATTRIBUTE_JOB_LIST atomically associates the process at
        # creation, closing the create-suspended/assign crash window (Windows 10+).
        check(update(attributes, 0, 0x0002000D, jobs, c.sizeof(jobs), None, None))
        startup = STARTUP_EX()
        startup.StartupInfo.cb = c.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100
        startup.StartupInfo.hStdInput = get_std(w.DWORD(-10 & 0xffffffff))
        startup.StartupInfo.hStdOutput = get_std(w.DWORD(-11 & 0xffffffff))
        startup.StartupInfo.hStdError = get_std(w.DWORD(-12 & 0xffffffff))
        startup.lpAttributeList = c.cast(attributes, c.c_void_p)
        command = c.create_unicode_buffer(subprocess.list2cmdline(sys.argv[3:]))
        # CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW.
        check(create_process(shutil.which(sys.argv[3]) or sys.argv[3], command, None, None, True, 0x4 | 0x80000 | 0x08000000, None, None, c.byref(startup), c.byref(process)))
        if resume(process.hThread) == 0xffffffff:
            raise OSError("Windows helper could not be resumed")
        close(process.hThread)
        process.hThread = None
        if wait(process.hProcess, 0xffffffff) != 0:
            raise OSError("Windows helper wait failed")
        code = w.DWORD()
        check(get_exit(process.hProcess, c.byref(code)))
        # The initial executable may have exited while its descendants still
        # hold a native credential lock. Do not expose closure until all exit.
        drain(job)
        return code.value
    finally:
        if process.hThread:
            close(process.hThread)
        if process.hProcess:
            close(process.hProcess)
        if attributes is not None:
            delete(attributes)
        close(job)
try:
    raise SystemExit(main())
except Exception:
    # No dependency messages, command arguments or tracebacks cross stderr.
    sys.stderr.write("Authentication helper ownership setup or cleanup failed.\n")
    raise SystemExit(1)
`;
