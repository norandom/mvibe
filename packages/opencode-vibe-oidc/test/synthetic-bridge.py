"""Synthetic stdio helper and descendant. No auth imports, sockets, browsers or keyring."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(sys.argv[2])
mode = sys.argv[1]
if mode == "sentinel":
    print(os.getpid(), flush=True)
    time.sleep(120)
    raise SystemExit
if mode == "worker":
    lock = (root / "owned.lock").open("a+b")
    lock.write(b"0")
    lock.flush()
    if sys.platform == "win32":
        import msvcrt
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print(os.getpid(), flush=True)
    time.sleep(120)
    raise SystemExit

worker = subprocess.Popen([sys.executable, "-u", __file__, "worker", str(root)], stdout=subprocess.PIPE)
worker_pid = int(worker.stdout.readline().strip())
with (root / "pids.jsonl").open("a") as out:
    out.write(json.dumps([os.getpid(), worker_pid]) + "\n")
if mode == "exit-first":
    os._exit(0)
def reply(request, result):
    sys.stdout.buffer.write((json.dumps(dict(version=1, id=request["id"], ok=True, result=result)) + "\n").encode()); sys.stdout.buffer.flush()
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "hello":
        reply(request, dict(protocol=1, api_version="1.0.0", audience="https://inference.example.test"))
    elif method == "status":
        reply(request, dict(signed_in=(root / "committed").exists(), config_match=True))
        if mode == "exit-after-status" and not (root / "committed").exists():
            (root / "committed").write_text("synthetic committed revision", encoding="utf8")
            os._exit(0)
    elif method == "resolve":
        # The host cannot know this write completed when the reply never arrives.
        (root / "committed").write_text("synthetic committed revision", encoding="utf8")
        (root / "resolving").touch()
    elif method == "shutdown":
        if mode == "stubborn":
            continue
        # Simulate a final persistence worker drain before closing.
        time.sleep(0.15)
        worker.terminate()
        worker.wait(timeout=2)
        (root / "drained").touch()
        reply(request, dict(closed=True))
        break
else:
    worker.terminate()
    worker.wait(timeout=2)
