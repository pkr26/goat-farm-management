# Firefox runtime verification

The macOS 27 bundled Firefox launch failed before the first test reached the app. The original serial run and its explicit 180-second launch timeout remain in `firefox-initial-launch.log` and `initial-receipt.json`. Independent fresh-profile headed/headless probes also timed out. The native sample pointed to the macOS event loop; matching upstream reports describe an application-data/TCC startup issue. That explanation is an inference, not a locally proven permission diagnosis. No personal profile, TCC grant, browser re-signing, sandbox override, cached-binary change, or application/config edit was used.

To complete the browser gate, the test runner uses the official version-matched Linux Playwright runtime. This validates Linux Firefox, not successful Firefox launch on this macOS host. The original local application, API, test configuration, assertions, and serial global setup are retained.

## Runtime

- Official image: `mcr.microsoft.com/playwright:v1.62.1-noble`
- Digest: `sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e`
- Linux ARM64; client/server Playwright 1.62.1; bundled Firefox 153.0/revision 1538.
- Task-owned container: `herdly-firefox-runtime-e80a4878c80f`; user `pwuser`; zero host mounts; no additional capabilities or privileged mode.
- Only published host binding: `127.0.0.1:61880` → container port 3001.
- The complete argv, image/container metadata, server package version, and final probe are in the adjacent JSON receipts.

```sh
docker pull mcr.microsoft.com/playwright:v1.62.1-noble
docker run --detach --rm --init \
  --name herdly-firefox-runtime-e80a4878c80f \
  --publish 127.0.0.1:61880:3001 \
  --workdir /home/pwuser --user pwuser \
  mcr.microsoft.com/playwright:v1.62.1-noble \
  /bin/sh -c 'npx -y playwright@1.62.1 run-server --port 3001 --host 0.0.0.0'
```

The parent runs its original serial Firefox command with these environment values:

```sh
E2E_BROWSER=firefox
PW_TEST_CONNECT_WS_ENDPOINT=ws://127.0.0.1:61880/
PW_TEST_CONNECT_EXPOSE_NETWORK='<loopback>'
```

Playwright's supported loopback network bridge lets the remote browser reach the unchanged localhost services. No repository configuration was changed. The official documentation requires matching client/server versions and documents the remote server and network exposure: [Docker remote connection](https://playwright.dev/docs/docker#remote-connection), [BrowserType.connect](https://playwright.dev/docs/api/class-browsertype#browser-type-connect).

## Probe result and timeout distinction

The final one-shot probe completed with exit 0 under an explicit 20-second external process deadline. In 843ms it connected to Firefox 153.0, loaded the expected data-URL title, and received HTTP 200 from `http://localhost:3000/login`. Its fresh private context had JavaScript disabled, with an additional `/api/**` abort route; the request receipt has one `/login` request and zero API requests. It used no credentials, stored authentication, global setup, trace, or shared E2E-state write.

The first diagnostic process reached its external 20-second deadline. The second instrumented run showed the browser/version/data-URL/app HTTP 200 checks had already completed successfully in 832ms and written their receipt; residual Node network-bridge handles kept the one-shot process alive after its owned context/browser were closed. The final diagnostic exits explicitly after those closures and receipt output. Both earlier process-deadline logs are retained. They are process-lifetime failures, not evidence of a failed Linux browser launch or failed app response. The first probe's detailed milestones were not retained, so no stronger claim is made about that attempt.

## Gate and cleanup

The parent confirmed the final serial full Linux Firefox gate completed with **71 passed, 0 failed, 0 retries, 0 skipped, exit 0, 3.1 minutes**. The original application/configuration and complete business/a11y assertions were retained. Its durable full-run evidence is [firefox.log](../final-verification/firefox.log) and [firefox-receipt.json](../final-verification/firefox-receipt.json).

After that gate completed, `docker stop herdly-firefox-runtime-e80a4878c80f` exited 0. Docker's `--rm` removal completed and a subsequent inspect reported that the owned container was absent. The original failed native runner/browser and both standalone probe launch PIDs were also absent; the targeted bundled Firefox 1538 process scan found no remaining browser/helper process. The official image remains as a reproducible cache. No unrelated container, image, cache, or running service was changed. The stop, absence, native-process checks, and retained-image digest are recorded in `firefox-linux-cleanup-receipt.json`.

Upstream macOS startup references: [Playwright issue 42768](https://github.com/microsoft/playwright/issues/42768), [Playwright issue 42082](https://github.com/microsoft/playwright/issues/42082), [Mozilla bug 2060476](https://bugzilla.mozilla.org/show_bug.cgi?id=2060476), [Mozilla bug 2069536](https://bugzilla.mozilla.org/show_bug.cgi?id=2069536).
