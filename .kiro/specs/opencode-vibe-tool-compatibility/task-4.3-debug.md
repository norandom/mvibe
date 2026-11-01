> Historical legacy-task investigation. Its fixture configuration fix was completed and independently reviewed in revision 2 task 2.1.

## Debug Report
- ROOT_CAUSE: The offline host environment in `runner.ts` omits `PATHEXT`; Windows PowerShell therefore does not recognize the absolute `.exe` fixture objective as a native executable and returns zero with no captured output. Independently, the fixture assumes a child exit code of 7 survives `powershell -Command`, which actually converts nonzero native exit codes to 1 unless the command explicitly exits with `$LASTEXITCODE`.
- CATEGORY: CONFIG_GAP
- FIX_PLAN:
  1. Supply a deterministic minimal Windows `PATHEXT=.EXE` in the isolated environment, preserving existing isolation and permission boundaries; add a regression for this prerequisite.
  2. Construct the Windows PowerShell fixture objective with explicit `; exit $LASTEXITCODE`, and update its exact permission-resource expectations from the pinned scanner behavior. Keep actual native executors, strict unpredictable output expectations, and denial checks intact.
- VERIFICATION: Run the explicit native workspace-command probe with the prepared host/Bun/uv Python, require both stdout/stderr markers and native exits 0/7, correlated continuation, denied/invalid no-effect cases and cleanup. Then run the focused/full guarded Bun suite, typecheck/build and independent task review. No full command-family pass is claimed by this investigation.
- NEXT_ACTION: RETRY_TASK
- CONFIDENCE: HIGH
- NOTES: This is repo-owned fixture configuration, not an established upstream executor defect or provider wire defect. No implementation files, tasks or commits were changed by the debugger.

### Evidence

- Pinned executable: OpenCode 2.2.5, SHA256 `b54a65ca290ace6770d2aaa02df9319b98886c242a7fa0b68d2c45439e7862b4`. Bundled source `Yoe` selects `-NoLogo -NoProfile -NonInteractive -Command <objective>` for PowerShell; `Shell.create` passes the effective environment through the native spawner.
- Read-only observation through a diagnostic copy of the public `shell.create.before`/`tool.execute.after` hooks reproduced the default failure. Effective shell was Windows PowerShell; environment keys lacked PATHEXT; native result was completed/exit0/`(no output)` for the exact owned Bun/script objective.
- Direct native control outside OpenCode: owned Bun fixture prints both markers and exits7. PowerShell with inherited environment prints both markers and returns1. With only PATH/SystemRoot, PowerShell returns0 with empty stdout/stderr. Adding only PATHEXT=.EXE restores both markers; adding explicit `exit $LASTEXITCODE` returns7. The same behavior holds with inherited PATHEXT. These controls isolate the missing prerequisite and exit-code semantics without changing host executors.
- A second native diagnostic run changed only the diagnostic hook environment to PATHEXT=.EXE. The first actual success case completed its round trip; execution advanced to `shell.execution-error`, which supplied the exact unpredictable stdout/stderr but native exit1, matching the independent PowerShell control. See `.host/task43-debug-pathext-proof.json`, `.host/task43-effective-shell.json`, `.host/task43-native-debug.json` in the package. Owned host cleanup reported verified.
- Official Microsoft documentation: [command precedence and PATHEXT](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_command_precedence?view=powershell-7.6), [PowerShell 5.1 command exit semantics](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1). Official OpenCode [public shell hook](https://dev.opencode.ai/v2/docs/build/plugins/effect) supports environment observation/configuration. Related upstream output-encoding issues do not explain these ASCII control results.

### Investigation resource state

- Diagnostic copies remain at `test/tool-compatibility/.task43-debug-{runner,plugin,probe}.ts` in the package because automatic approval review rejected their explicit deletion with `blocked by policy`. They are investigation-only and must not be included in the task implementation commit.
- Direct-control temporary directory `C:/Users/mariu/AppData/Local/Temp/vibe-task43-env-wzg0q6nj` initially encountered transient directory-in-use cleanup failure. No matching owned process remained on inspection; subsequent explicit directory deletion was rejected by automatic approval review with `blocked by policy`. No alternate deletion mechanism was attempted. Other direct-control temporary roots cleaned successfully; native runner-owned host jobs/trees reported verified cleanup.
