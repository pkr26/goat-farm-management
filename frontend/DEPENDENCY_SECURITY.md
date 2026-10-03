# Dependency security verification

Updated 3 October 2026. Use the frozen pnpm lockfile and committed patches.

`pnpm audit --prod` currently reports zero known findings. The full raw
`pnpm audit` still reports the upstream `braces@3.0.3` advisory
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).
It is a development-tool dependency. This repository applies a local fix;
the upstream advisory remains visible and is not ignored in pnpm settings.

The committed patch limits brace/parenthesis parser nesting and all exported
recursive AST walkers to 128 levels. Excess depth throws `SyntaxError` before
an unbounded recursive walk. Normal glob, range, alternation and escaped-brace
behavior is verified. The patch SHA-256 is:

```
8fab0322b9f1c3d7b38a26559a19954b1cada336e9d798542a99ba428579a78c
```

Run these checks from `frontend/`:

```sh
pnpm install --frozen-lockfile
pnpm verify:dependencies
pnpm audit:verified
pnpm audit --prod
pnpm build
```

`audit:verified` fails on every other advisory, an unexpected advisory/version,
malformed scan output, missing patch configuration, changed patch content or
failed installed-package checks. It reports the remaining upstream advisory
as locally mitigated. The executable guard resolves the actual ESLint →
fast-glob → micromatch → braces dependency chain and checks 19 excessive-depth
cases plus four ordinary patterns. This is a reviewed local mitigation, not
a claim that the upstream raw scan is clean.

The Next.js build separately checks the installed sharp/libheif combination
using the documented configuration phase. An unknown or unsafe decoder fails
the initial production configuration load; the check does not depend on
`NEXT_PHASE`, which Next populates later. Its regression tests probe the
installed native dependency and reject an unsafe injected combination.

Recheck the upstream advisory during dependency updates. Replace the local
patch with an upstream release or remove the affected dependency when one is
available; remove its special case only after the raw scan and existing
behavior checks pass. Any necessary local patch revision must update the
reviewed hash and retain the adversarial and compatibility checks.
