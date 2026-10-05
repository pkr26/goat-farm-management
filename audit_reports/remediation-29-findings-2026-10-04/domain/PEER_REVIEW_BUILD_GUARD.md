# Independent review of the isolated image-dependency probe

Reviewed on 2026-10-04 by the domain reviewer, independently of the frontend author. **No blocking correctness or security issue found.** This was a read-only implementation/evidence review; the reviewer did not rerun the frontend tests or change application files.

`defaultSharpProbe()` invokes the current `process.execPath` with fixed CommonJS arguments and no shell. The requested installation root is the child working directory. A 10-second timeout uses `SIGKILL`, and captured output is limited to 16 KiB. Spawn errors, signals, nonzero exit status, invalid JSON and malformed sharp/libvips/libheif versions produce `unloadable`. A missing HEIF version is accepted only after validating sharp and libvips metadata. Production-build enforcement and the existing minimum libheif policy remain intact.

The [verified focused receipt](../frontend/isolated-probe-tests-verified.log) reports **37 tests passed across three files in 12.32 seconds**. Reviewed controls use real child fixture packages to cover unsafe installed versions, parent module-cache isolation, valid decoder absence, nonzero/signal exits despite safe-looking stdout, output overflow, missing cwd, malformed/empty output and a non-exiting process that ignores SIGTERM. This receipt establishes the focused contracts, not whole-application or image acceptance.

## Standalone and diagnostic evidence

The pinned Next 16.3.8 implementation independently loads sharp in `frontend/node_modules/next/dist/server/image-optimizer.js` (`getSharp`). `frontend/node_modules/next/dist/build/collect-build-traces.js` traces the standalone server/image optimizer; removing the guard's in-process require does not remove that runtime dependency. `frontend/node_modules/next/dist/build/utils.js` writes standalone `server.js` with serialized `nextConfig` and `__NEXT_PRIVATE_STANDALONE_CONFIG`. The Docker runner copies this output and starts `node server.js`; this build-time probe introduces no new standalone runtime child-process requirement. Final image contents, startup and image scanning still require the normal artifact checks.

The [AMD64 diagnosis](../frontend/amd64-build-diagnosis.md) preserves the controlled sequence: [plain tiny Next build passes](../frontend/minimal-next-amd64-pnpm.json), [actual in-process guard triggers the teardown assertion](../frontend/minimal-next-amd64-pnpm-guard.json), and [isolated native probe passes](../frontend/minimal-next-amd64-pnpm-guard-isolated.json). These observations support isolating this interaction in the tested ARM64-hosted AMD64 environment. They do not identify the component that invalidated the descriptor, prove an emulator root cause, or establish completed multiarchitecture production acceptance.

## Reviewed source snapshot

Paths are repository-relative. Installed Next files are local dependency evidence, not newly committed project sources. SHA-256 values below identify the exact reviewed contents.

| Path | SHA-256 |
| --- | --- |
| `frontend/src/lib/image-deps-guard.ts` | `985cf2de1ff2980d49e414b371c25381c74c1f649f94331e231adfc417362d0d` |
| `frontend/src/lib/image-deps-guard.test.ts` | `2d1ca0307fc93d25b1f777ad8034ba11d1771318f052d20b8fa0a9502d77f313` |
| `frontend/src/lib/image-deps-guard.equals.test.ts` | `62edd02ba0be8f9b0b88019522e74dcc6bf051fe030cddba5d5e5ddf7ab1329a` |
| `frontend/src/lib/image-deps-guard-probe.test.ts` | `b1b1b3ac39e8444787a0cf0b16f9e33a51aa3490487971edfb58b4c8e7add592` |
| `frontend/next.config.ts` | `e98aa6ec88633c262900573bb45007ad9c8cdcda067f87261ec0541a463c2f48` |
| `frontend/Dockerfile` | `95ae2f5a8d9a4446624000d0673502de39a26b82476c278849f7c1290ee693cb` |
| `frontend/node_modules/next/dist/build/utils.js` | `de18448b9bb78b8b53ddf50caecca755cbc99031ba3609ada337c65fb11ed284` |
| `frontend/node_modules/next/dist/build/collect-build-traces.js` | `2c3df107e4910925568c9c986a8100e093d19d6e0bb0e316b927f752cec93c96` |
| `frontend/node_modules/next/dist/server/image-optimizer.js` | `38b92fde5bc72aa23c999d283052da11a6dfb36be75c2d92683d539551065a3c` |
