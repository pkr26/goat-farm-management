# AMD64 frontend build: native probe/Next teardown reduction

Status: fixed and validated. Subprocess isolation retains the fail-closed native-version policy. The focused 37-test suite and final full frontend coverage (328 files / 5,293 tests) pass, as do lint, TypeScript, both clean image builds, standalone/native-dependency smokes, and vulnerability scans.

The original two failed project builds are preserved in the [ops initial log](../ops/frontend-amd64-build.log) and [focused repeat](../ops/frontend-amd64-focused-rerun.log). Both finish compilation, TypeScript checking and route listing, then assert in libuv. An unchanged retry was not used for this investigation.

## Environment and controlled results

All new controls use the exact failing intermediate dependency/source image `be94e1d9583733ac40bf9c859495c19a025296bf2fcc0000e40e42b0150afa52`, with Node 24.21.0, libuv 1.52.1, Next 16.3.8, React 19.2.8 and sharp 0.35.4 / libheif 1.23.2. The AMD64 container runs through QEMU on the local ARM64 Colima Docker VM. This observation alone does not attribute the cause to QEMU.

| Control | Outcome | Evidence |
| --- | --- | --- |
| Ops Node child-process lifecycle, both architectures | 24 children per architecture pass | [Ops control](../ops/node-emulation-control.json) |
| Tiny standalone Next app; direct Node, telemetry off | Build and clean exit 0, 35.9s | [Receipt](minimal-next-amd64.json), [log](minimal-next-amd64.log) |
| Same tiny app; pnpm 9.15.9, default telemetry | Build and clean exit 0, 43.1s | [Receipt](minimal-next-amd64-pnpm.json), [log](minimal-next-amd64-pnpm.log) |
| Bare native sharp load/version read and exit | Exit 0, 1.2s | [Receipt](sharp-lifecycle-amd64.json) |
| Tiny Next app with actual production `assertImageDecodeSafetyForBuild()` config call | Same libuv assertion after the two-route table | [Receipt](minimal-next-amd64-pnpm-guard.json), [log](minimal-next-amd64-pnpm-guard.log) |
| Same actual guard, installed native sharp version probe in a bounded subprocess | Guard retained; build and clean exit 0, 41.0s | [Receipt](minimal-next-amd64-pnpm-guard-isolated.json), [log](minimal-next-amd64-pnpm-guard-isolated.log) |
| Full project, observational epoll interposer only | Same assertion; actual failing errno and descriptors identified | [Receipt](instrumented-next-amd64.json), [log](instrumented-next-amd64.log) |

The tiny apps import no farm application UI. The guard variant imports the actual existing safety guard from `src/lib/image-deps-guard.ts`. The isolation variant obtains the same installed sharp native version object with `spawnSync(process.execPath, ...)`, with a 10-second timeout and a required successful child exit, then supplies it through the existing `sharpProbe` dependency parameter. It does not bypass the libheif safety policy. The first direct-Node control initially declared older package version strings in its temporary manifest; it installed nothing and explicitly printed the actual Next 16.3.8 tree used from the image. The retained reproducer now declares the exact installed versions.

This A/B/C sequence isolates config-time native sharp loading plus the Next build lifecycle as a prerequisite for the reduced failure in this environment. The exact internal component closing the event-loop descriptor is still unidentified. It does not establish a general sharp failure, a farm application failure, or an emulator-specific bug.

## Actual syscall failure

The full instrumented run printed immediately before the original assertion:

```text
EPOLL_DIAGNOSTIC pid=39 tid=39 epfd=3(<readlink errno=2>) op=1 fd=6(pipe:[11126352]) events=1 result=-1 errno=9
Assertion failed: errno == EEXIST (../deps/uv/src/unix/linux.c: uv__io_poll: 1427)
```

The syscall failed with EBADF (9). Immediately after that failure, descriptor 3 was missing from `/proc/self/fd`, while descriptor 6 resolved to a pipe. This points to an invalid event-loop epoll descriptor at teardown, not a filesystem path collision. The assertion itself does not reveal the actual errno: [Node 24.21.0 libuv source](https://github.com/nodejs/node/blob/v24.21.0/deps/uv/src/unix/linux.c#L1423-L1431) checks for the expected `EEXIST` retry case after a failed `EPOLL_CTL_ADD`. The [retrieved exact source hash and lines](libuv-source-observation.json) anchor that interpretation.

The [C interposer](epoll_diagnostic.c) preserves syscall arguments, return values and errno. Its known-invalid-FD [control](epoll_diagnostic_control.c) returned `-1` / EBADF both with and without instrumentation; [compile/control log](epoll-diagnostic-compile.log). Instrumentation can affect timing, so a success under it would not have replaced ordinary build acceptance; here it reproduced the failure.

The aborted build generated an approximately 3GB AArch64 ELF core from the QEMU-hosted process. The attempted [GDB read](epoll-diagnostic-core-stack.log) yielded no useful guest stack. Core architecture is not proof that QEMU caused the guest failure. The owned core and diagnostic container were removed; only useful text receipts remain.

The failed Next children left sleeping pnpm wrapper processes. After preserving the errors, those two owned diagnostic wrappers were explicitly stopped: [full-build stop record](instrumented-next-amd64-stop.json) and [tiny-guard stop record](minimal-next-amd64-pnpm-guard-stop.json). Their recorded exit 137 is this deliberate cleanup, not an OOM claim or the Next command's successful exit. Both inspected containers reported `OOMKilled: false`.

## Reproduction and next step

The [bounded tiny-build reproducer](probe_minimal_next_build.py) creates and removes only its named diagnostic containers. After the original experiments exposed multi-gigabyte core dumps, both retained diagnostic scripts were updated to run future diagnostic containers with `--ulimit core=0`; this limits core files, not build exit handling, and changes no product default. With the retained input image available:

```sh
python3 audit_reports/remediation-29-findings-2026-10-04/frontend/probe_minimal_next_build.py
python3 audit_reports/remediation-29-findings-2026-10-04/frontend/probe_minimal_next_build.py --pnpm
python3 audit_reports/remediation-29-findings-2026-10-04/frontend/probe_minimal_next_build.py --pnpm --guard
python3 audit_reports/remediation-29-findings-2026-10-04/frontend/probe_minimal_next_build.py --pnpm --guard --isolate-sharp
```

The [instrumented full-build script](probe_instrumented_next_build.py) documents the isolated shared-object prerequisite and enforces a 480-second bound. No further unchanged full build is justified by these results.

The production change in `frontend/src/lib/image-deps-guard.ts` now reads the native report using the current Node interpreter in the requested install directory, with a 10-second timeout, SIGKILL termination, and a 16KB output bound. It rejects child failure, signal termination, startup failure, empty/invalid JSON and malformed version metadata. A missing HEIF decoder is accepted only after valid sharp and libvips version entries are established; a vulnerable HEIF version still fails the existing production policy. The parent no longer loads the native sharp module.

The new `image-deps-guard-probe.test.ts` uses real fixture-package subprocesses, including a real child that ignores SIGTERM and remains alive after printing a safe report. It is terminated at the bound and rejected. Other cases cover a requested install tree containing vulnerable HEIF, unchanged parent module cache, actual nonzero/signal exits, excessive output, missing working directory, malformed reports and a valid no-decoder report. The maintained shipped-dependency test also uses the real isolated probe. [Final focused result: 37 tests](isolated-probe-tests-final.log), [lint](isolated-probe-lint-final.log), [integrated typecheck](isolated-probe-typecheck-final.log).

An initial test attempt mocked the Node builtin, but the runtime retained the real module binding; the [failed fixture log](isolated-probe-tests.log) is retained. Replacing those ineffective mocks with real child processes exercised the actual boundaries and produced the passing final receipt; no production validation was relaxed.

The original pre-isolation full coverage run is retained in [its log](full-coverage-before-probe-isolation.log) and [reports](coverage-before-probe-isolation/index.html). The final complete run passes 328 files / 5,293 tests in 381.52s with all unchanged global, layer and entrypoint floors; [final log](full-coverage.log), [summary](coverage/coverage-summary.json). The guard itself has 100% statement, line and function coverage and 96.61% branch coverage. An independent peer review found no blocking issue and checked Next's serialized standalone configuration plus runtime sharp tracing.

The ops agent then ran normal full builds from the corrected source, with no instrumentation, changed exit handling or safety bypass:

| Final image | Normal build | Immutable local image ID |
| --- | --- | --- |
| Linux ARM64 | Exit 0, 37.79s | `sha256:5a1e68466ba2a49ba5b48b402414b50807fbea19fcc2fd43b49c06a33b9168ce` |
| Linux AMD64 | Exit 0, 174.33s | `sha256:efcce7b2773bb8cfa0350ae4ae42e8fe4004230d3677c6683bfdcd2f36cc4d79` |

Both images then passed standalone `/healthz` and direct installed-native-sharp version smokes. Both raw all-severity and unchanged repository-policy vulnerability scans reported zero vulnerability records, using the recorded fresh Trivy database; SBOMs are retained. [Build receipts](../ops/frontend-final-builds.json), [runtime receipts](../ops/frontend-final-runtime.json), [scan/database/SBOM receipts](../ops/frontend-final-scan-manifest.json).

This resolves the observed AMD64 artifact build failure without weakening the decoder or vulnerability gates. AMD64 was built and smoke-tested under local QEMU; a separate native AMD64 host was not exercised. The exact native component closing the descriptor remains unidentified, so the report attributes the observed interaction rather than an unproven upstream root cause.
