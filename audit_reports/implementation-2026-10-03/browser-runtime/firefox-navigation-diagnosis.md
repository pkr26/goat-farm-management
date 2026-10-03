# Firefox navigation synchronization diagnosis (ADD12)

The first full Linux Firefox gate reached the application and completed with 69 passed / 2 failed. Both failures were `page.goto: NS_BINDING_ABORTED`, after the earlier business checks had succeeded. No assertion was suppressed or removed.

The adjacent sanitized trace extracts retain action/request URLs, methods, timing, response status, and frame URL metadata only. They exclude headers, request/response bodies, cookies, tokens, credentials, and HTML snapshots. Unrecognized query values are redacted. Raw failure traces were moved outside the repository by the parent.

## Purchases

`frontend/src/app/(app)/purchases/page.tsx` closes batch detail optimistically, then `closeDetail()` writes `batch: null` through `useUrlState`. That hook dispatches asynchronous `router.replace(..., { scroll: false })`.

- Keyboard Escape completed at trace time 313343.049ms.
- The dialog-hidden assertion completed at 313350.418ms, but frame URL metadata still showed `/purchases?batch=13`.
- The pending RSC GET for `/purchases` started at 313342.955ms.
- The test's hard `goto('/animals?bucket=QUARANTINE')` began at 313351.999ms. Its document GET received HTTP 200.
- At 313359.242ms, Next emitted its RSC-fetch fallback for `/purchases`.
- The goto failed at 313359.724ms; a replacement document GET for `/purchases` followed at 313361.541ms.

`frontend/e2e/purchases.spec.ts` now checks that the pathname is `/purchases` and the `batch` query parameter is absent after closing detail, before starting the next hard navigation.

## Tasks

`frontend/src/app/(app)/tasks/page.tsx` applies `navigationOverride` immediately when a tab is clicked, then dispatches asynchronous `router.replace` for the shareable tab URL. Cached rows can therefore satisfy visible-row assertions before that URL commits.

- The Overdue tab click completed at 334663.087ms, while frame URL metadata still showed `/tasks`.
- The pending RSC GET for `/tasks?tab=overdue` started at 334662.867ms.
- The test's hard `goto('/breeding')` began at 334689.828ms.
- Next emitted its RSC-fetch fallback for `/tasks?tab=overdue` at 334693.304ms.
- The goto failed at 334695.041ms; a replacement document GET for `/tasks?tab=overdue` followed at 334696.722ms.

`frontend/e2e/tasks-guards.spec.ts` now checks the `/tasks` pathname and `tab=overdue` query before its next helper starts a hard navigation.

## Interpretation and verification

Both observed failures are caused by the test initiating a full document navigation before the previous asynchronous App Router state update commits. Interrupting that pending RSC fetch produces Next's fallback document navigation to the old route, which aborts the new goto. The installed Next 16.3.8 `fetch-server-response.js` fallback returns the original URL on a failed RSC fetch; the runtime trace records the same fallback route. These traces do not show a failure of the underlying purchase or duty behavior.

The two semantic URL assertions verify agreement between the optimistic UI and the persisted route before navigation. They introduce no sleep, retry, exception catch, or reduced business assertion. Only the two E2E specs changed; app, shared helper, unit, build, and production sources remained unchanged.

The focused run executed both original Firefox journeys with the original Playwright configuration and the version-matched Linux runtime: **2 passed, 0 failed, 0 retries, 12.1 seconds, exit 0**. Scoped ESLint and TypeScript checking (`tsc --noEmit`) exited 0. Exact command/environment and the log are retained in `firefox-url-commit-focused-receipt.json` and `firefox-url-commit-focused.log`. The parent will run the final full matrix serially.
