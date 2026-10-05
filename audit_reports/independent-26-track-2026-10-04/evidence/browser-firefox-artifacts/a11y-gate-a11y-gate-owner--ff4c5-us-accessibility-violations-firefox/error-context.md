# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: a11y-gate.spec.ts >> a11y gate >> /owner (en) has no serious accessibility violations
- Location: e2e/a11y-gate.spec.ts:68:7

# Error details

```
TimeoutError: browserType.launch: Timeout 180000ms exceeded.
Call log:
  - <launching> /Users/pradeepreddy/Library/Caches/ms-playwright/firefox-1538/firefox/Nightly.app/Contents/MacOS/firefox -no-remote -headless -profile /var/folders/yy/8vxx571s72vbb5jg4zt6v2fm0000gn/T/playwright_firefoxdev_profile-5WcYqE -juggler-pipe -silent
  - <launched> pid=82750
  - [pid=82750][err] *** You are running in headless mode.
  - [pid=82750][err] sandbox_extension_issue_file_to_process failed for /Users/pradeepreddy/Library/Caches/ms-playwright/firefox-1538/firefox/Nightly.app/Contents/MacOS/plugin-container.app: 1 (Operation not permitted)
  - [pid=82750][out] Crash Annotation GraphicsCriticalError: |[0][GFX1-]: RenderCompositorSWGL failed mapping default framebuffer, no dt (t=0.361894) [GFX1-]: RenderCompositorSWGL failed mapping default framebuffer, no dt

```