# Firefox launch isolation control

The corrected control failed before any application interaction. Stock Playwright Firefox was launched headless with a 15-second timeout; it never returned a browser object, so `newPage()` and `about:blank` navigation were never reached. The child process recorded `TimeoutError: browserType.launch: Timeout 15000ms exceeded` after 15,007 ms and exited 1. Total harness duration was 15.814 seconds. Maximum observed Firefox CPU was 100.0%.

Literal browser diagnostics include `sandbox_extension_issue_file_to_process failed ... (Operation not permitted)` and `RenderCompositorSWGL failed mapping default framebuffer, no dt`. These identify the observed local launch/transport limitation; they do not establish a complete OS/graphics root-cause diagnosis. The independently reproduced failure cannot be attributed to an application route, API, database, or application port because none was used.

The wrapper tracked only descendants of its own Node process (PID 82558) and retained process snapshots. Playwright's timeout cleanup removed them; no manual signals were required and no owned processes remained. The outer 20-second guard was not reached. Existing browser/test processes belonging to the parent were not signaled.

Run from repository root:

```sh
python3 audit_reports/independent-26-track-2026-10-04/evidence/root/firefox-launch-control.py
```

The wrapper exits after retaining results; consult the result JSON's child `exit_code` (1), not the wrapper's own exit status. Evidence: `firefox-launch-control.mjs`, `.py`, `.stdout.log`, `.stderr.log`, and `.result.json`.

An initial audit-harness relative import typo (`Cannot find module '@playwright/test'`) occurred before launching Firefox. It is retained under `firefox-launch-control.initial-harness-error.*` for transparency and is not counted as a browser failure. The corrected attempt uses the same repository Playwright package and cached Firefox executable as the root run.
