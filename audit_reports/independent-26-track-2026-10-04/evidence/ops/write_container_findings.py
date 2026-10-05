"""Generate complete scanner evidence tables in this audit report only."""
import collections
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent
REPORT = OUT.parents[1] / "ops.md"
manifest = json.loads((OUT / "container-scan-manifest.json").read_text())

def rows(label, mode):
    report = json.loads((OUT / f"trivy-{label}-{mode}.json").read_text())
    return [(target, v) for target in report.get("Results", []) for v in target.get("Vulnerabilities", [])]

def table(label, mode):
    lines = ["| Package | Advisory | Installed | Fixed version | Scanner severity |",
             "|---|---|---|---|---|"]
    for target, v in rows(label, mode):
        lines.append(f"| `{v['PkgName']}` | {v['VulnerabilityID']} | `{v['InstalledVersion']}` | `{v.get('FixedVersion') or 'none listed'}` | {v['Severity']} |")
    return "\n".join(lines)

addition = """

## O26-07 — Medium — Current frontend Docker build fails on malformed Corepack package-manager declaration

**Tracks:** 22, 25. **Confidence:** high, actual current-source Docker build failure.

**Prerequisite:** Build the supplied frontend Dockerfile using its pinned Node image and committed package manifest. No attacker or unusual input is needed.

**Location:** `frontend/package.json:63`; `frontend/Dockerfile:4` enables Corepack and line 6 invokes the shim. The builder's later `pnpm build` at line 18 uses the same declaration.

**Reproduction:** `docker build -t goatfarm-a26-frontend:7245368 -f frontend/Dockerfile frontend` at the audited commit. The exact pinned Node image reaches dependency stage step 5, then exits 1: `Invalid package manager specification in package.json (pnpm@9.15.9+sha512-...==); expected a semver version`. A disposable copy with only `packageManager` changed to `pnpm@9.15.9` lets Corepack run the intended pnpm 9.15.9, exposing the distinct O26-08 failure. Source files were not edited.

**Expected versus actual:** The repository's own pinned build should bootstrap the declared package manager. Actual Corepack rejects the npm-style integrity suffix before package installation begins. A preinstalled local pnpm and successful local Next build do not exercise this path.

**Impact:** Fresh production frontend image builds and release workflows using this Dockerfile are blocked. This does not establish an outage in an already-deployed image.

**Recommendation:** Generate a valid Corepack `packageManager` declaration with the intended version and supported integrity format, and verify an actual clean Docker build in CI. Preserve integrity pinning rather than simply deleting it in the final fix.

**Evidence:** [frontend-container-build.txt](evidence/ops/frontend-container-build.txt), [frontend-container-input-results.json](evidence/ops/frontend-container-input-results.json), [probe_frontend_container_inputs.py](evidence/ops/probe_frontend_container_inputs.py).

## O26-08 — Medium — Frontend dependency layer omits the required local vulnerability patch

**Tracks:** 22, 25. **Confidence:** high, isolated failing case and passing control.

**Prerequisite:** O26-07 is corrected or otherwise bypassed so pnpm 9.15.9 reaches installation. This is a separate, latent build blocker, not the first error in the unmodified current build.

**Location:** `frontend/Dockerfile:5` copies only `package.json` and `pnpm-lock.yaml` before line 6 installs them; `frontend/package.json:78` and `frontend/pnpm-lock.yaml:22` require `patches/braces@3.0.3.patch`. The later `COPY . .` at Dockerfile line 17 occurs in the next stage, after installation must already have succeeded.

**Reproduction:** The retained probe copies exactly the two dependency-stage inputs into an owned temporary directory and corrects only the malformed package-manager value there. In the same pinned Node image, `corepack enable && pnpm --version && pnpm install --frozen-lockfile` reports 9.15.9 and exits 254 with `ENOENT: no such file or directory, open '/app/patches/braces@3.0.3.patch'`. Copying the existing repository `patches/` directory into that same fixture makes installation exit 0. The probe checks that the manifest bind mount exists; temporary files and installed dependencies were removed afterward.

**Expected versus actual:** All lockfile inputs needed for frozen installation should be present in the dependency layer. The required security patch is absent, so correcting O26-07 alone still cannot build the frontend image.

**Impact:** The production image cannot incorporate the committed dependency mitigation because its installation never completes. This finding does not assert that a successfully produced current image silently omitted the patch.

**Recommendation:** Copy the committed patch directory before `pnpm install` and include it in dependency-cache invalidation. Verify a clean Docker installation and the existing patched-dependency checks.

**Evidence:** [frontend-container-missing-patch.txt](evidence/ops/frontend-container-missing-patch.txt), [frontend-container-input-positive-control.txt](evidence/ops/frontend-container-input-positive-control.txt), [frontend-container-input-results.json](evidence/ops/frontend-container-input-results.json).

## O26-09 — Medium — Pinned backend and PostgreSQL images fail the current vulnerability policy

**Tracks:** 25, 22. **Confidence:** high for scanner/package-version and policy-gate results; runtime reachability/exploitation was not established.

**Prerequisite:** Build the current backend Dockerfile or use the current Compose PostgreSQL digest, then scan with the fresh 2026-10-04 vulnerability database and the repository's declared HIGH/CRITICAL, fixable-only policy.

**Location:** `Dockerfile:10` pins the backend Python base and deliberately does not upgrade its OS packages. `docker-compose.yml:54` and `.github/workflows/security.yml:140` pin PostgreSQL. The policy gates are `.github/workflows/security.yml:205` and line 221; `.trivyignore.compose-images` is scoped only to PostgreSQL.

**Reproduction:** Current backend and edge Dockerfiles were built directly from audited commit `7245368fa91333fb39387df789fa4ef9a58dbea3`, without source edits, deployment or registry writes. [scan_container_images.py](evidence/ops/scan_container_images.py) exports immutable local image IDs and scans their tarballs using `aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969`. The DB was freshly downloaded at `2026-10-04T22:37:24Z`, with `UpdatedAt=2026-10-04T19:39:34Z`; scans were then offline. All scanned images are Linux ARM64, so this is not evidence for the untested AMD64 variant. Exact image IDs/digests, DB SHA-256, commands, policy-file SHA-256 and all results are retained in [container-scan-manifest.json](evidence/ops/container-scan-manifest.json).

**Policy:** Vulnerability-only scans, HIGH/CRITICAL, `--ignore-unfixed`, exit code 1 for remaining findings. Only the PostgreSQL policy run loads the exact preserved `.trivyignore.compose-images`; backend/edge load no suppression file. Each image also has an unfiltered all-severity/all-fix-status report with no suppressions. The workflow's snakeoil-key exclusion concerns secret scanning; these runs scan vulnerabilities only and make no secret-scan claim.

**Expected versus actual:** The declared gate requires zero unsuppressed fixable HIGH/CRITICAL records. The actual current backend has **20 records (17 HIGH, 3 CRITICAL; 16 distinct advisories)** and exits 1. PostgreSQL still has **8 HIGH records (4 distinct advisories)** after its documented suppressions and exits 1. Current edge exits 0 with zero HIGH/CRITICAL records. The four raw pinned base/service scans are also preserved; base-only findings are not automatically attributed to a final application image that removes build tools. A current full frontend runtime could not be built because of O26-07/08, so its base scan is not represented as a completed runtime scan.

**Impact and limits:** These reproducible failures block the repository's stated security acceptance policy and show stale vulnerable package versions in shipped runtime inputs. Scanner severity is not the audit's application severity: no remote code execution, unauthenticated attack path or exposure of every listed component is established. For example, the Perl 32-bit advisory does not prove applicability to this ARM64 run; source-package matching can also cover optional modules. The confirmed finding is image freshness/policy noncompliance, assessed Medium, rather than 28 independently proven exploitable application vulnerabilities. Existing CI is expected to fail on this evidence; a gate bypass is not claimed.

**Recommendation:** Refresh the affected immutable bases/service digest to reviewed images with the fixes, or remove unneeded components in a reproducible build; rerun scans for both release architectures. Where an advisory is not applicable, document a component-specific, bounded exception with evidence instead of treating a scanner label as exploitability proof. Do not extend broad suppressions merely to make the gate green.

All unsuppressed package/advisory records failing those two runtime gates follow. Fixed versions are the fresh scanner's package-manager recommendations and have not been installed by this audit.

### Current backend image

"""
addition += table("backend-current-build", "repository-policy")
addition += "\n\n### Pinned PostgreSQL service image, after repository suppressions\n\n"
addition += table("postgres-service", "repository-policy")
addition += "\n\n**Evidence:** [backend-container-build.txt](evidence/ops/backend-container-build.txt), [edge-container-build.txt](evidence/ops/edge-container-build.txt), [container-scan-summary.txt](evidence/ops/container-scan-summary.txt), [container-scan-manifest.json](evidence/ops/container-scan-manifest.json), [complete raw scanner inventory](container-vulnerability-inventory.md), and the individual `trivy-*-raw.json` / `trivy-*-repository-policy.json` reports in [evidence/ops](evidence/ops). No pre-existing app image was used as current-build evidence.\n"
current = REPORT.read_text()
assert "## O26-07" not in current
REPORT.write_text(current + addition)

inventory = ["# Complete current container scanner inventory", "",
             "This is raw scanner inventory, not a claim that every advisory is reachable or exploitable in this application. It preserves every record, including unfixed, lower-severity and explicitly suppressed records. The actionable policy failure is O26-09 in ops.md; current frontend runtime construction is blocked by O26-07/08. Images are ARM64. Exact identity, database and suppression policy are in evidence/ops/container-scan-manifest.json.", ""]
for record in manifest["images"]:
    label = record["label"]
    inventory += [f"## {label}", "", f"Requested reference: `{record['requested_ref']}`.", "", f"Scanned immutable ID: `{record['immutable_local_image_id']}`.", "", table(label, "raw"), ""]
(REPORT.parent / "container-vulnerability-inventory.md").write_text("\n".join(inventory))
print(json.dumps({"report": str(REPORT), "runtime_policy_records": {label: len(rows(label, "repository-policy")) for label in ("backend-current-build", "postgres-service")}}, indent=2))
