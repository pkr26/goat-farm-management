"""Assemble the complete, single-file audit from adjudicated primary reports.

Run only after updating evidence/validation-summary.json with final outcomes.
"""
from pathlib import Path
import collections
import json
import re

BASE = Path(__file__).resolve().parents[2]
validation = json.loads((BASE / "evidence/validation-summary.json").read_text())
manifest = json.loads((BASE / "evidence/source-manifest.json").read_text())
sources = ["security-api.md", "domain.md", "frontend.md", "ops.md"]
issues = []
for source in sources:
    body = (BASE / source).read_text()
    matches = list(re.finditer(r"^#{2,3} ([DFOS]26-\d+) — (.+)$", body, re.M))
    for i, match in enumerate(matches):
        ident, title = match.groups()
        chunk = body[match.end():matches[i+1].start() if i+1 < len(matches) else len(body)]
        if ident.startswith("D"):
            severity = "Medium" if title.startswith("P2:") else "Low"
            title = re.sub(r"^P[23]:\s*", "", title)
        elif ident.startswith("S"):
            severity = "Low"
        else:
            severity, title = title.split(" — ", 1)
        issues.append({"id":ident,"severity":severity,"title":title,"source_report":source})
assert len({i["id"] for i in issues}) == len(issues)
rank = {"Critical":0,"High":1,"Medium":2,"Low":3}
issues.sort(key=lambda item:(rank[item["severity"]], item["id"]))
counts = collections.Counter(item["severity"] for item in issues)

tracks = [
 (1,"Architecture and maintainability","API/service/model boundaries, large frontend modules, shared state and worker configuration dependencies.","O26-01; see frontend architecture review"),
 (2,"Functional user journeys","Owner/worker/browser journeys, record entry, screening capture/review and error states.","D26-01; F26-02,04,05,08,09"),
 (3,"Goat-farm business rules","Animal chronology, reproductive and bucket transitions, mortality attribution, health and task rules.","D26-01,06"),
 (4,"Financial and inventory accuracy","Ledger/inventory writes, insurance and source attribution; first-day feeding; disposal accounting.","D26-02,04,07"),
 (5,"Simulation and forecasting","Calibration, cohort/financial transformations, seasonal prices and independently constructed numerical controls.","D26-02,06,07,08,09"),
 (6,"AI photo-screening quality","Image/provider contracts, gate/specialist evidence preservation, reviewability and full synthetic worker cycles.","D26-03,05; F26-04; O26-01"),
 (7,"Authentication and sessions","Password/PIN/TOTP, refresh and revocation, exact-session ownership and credential reset.","S26-02; no new authentication bypass reproduced"),
 (8,"Permissions and farm isolation","Recursive route/dependency inventory, membership/role locks, owner-only routes and real-DB regression matrix.","No new tenant-isolation defect confirmed"),
 (9,"Application security","Credential/origin handling, input/body limits, browser security headers, dangerous execution/HTML source searches.","S26-01"),
 (10,"Privacy and data lifecycle","Account exports/tombstones, photo/credential retention, notification-phone scrubbing and traceability.","S26-02; deployment/legal retention decisions not certified"),
 (11,"Database integrity","Constraints/tenant relationships, direct SQL backstops, catalog state and persisted chronology.","D26-01; cross-tenant FK control passed"),
 (12,"Database migrations","Revision inventory, explicit-target preflights, complete empty-schema upgrade/downgrade/upgrade plus drift checks and existing migration regressions.","Full empty-schema roundtrip passed; no new migration defect confirmed; populated historical upgrades not exhaustively rehearsed"),
 (13,"Concurrency and duplicate prevention","Authorization/domain lock order, idempotency, outboxes and independent concurrent-sale probe.","No new race confirmed in tested paths; sale control produced 200/409 and one transaction"),
 (14,"API contracts and integration","Fresh OpenAPI equality and 126 runtime-route dependency records, generated transport and frontend permissions.","S26-01; F26-08; contract equality passed"),
 (15,"Frontend state correctness","Farm changes, async ownership, active uploads, localization render order, unavailable-data states.","F26-04,05,06,09"),
 (16,"Offline and PWA reliability","Actual service-worker execution, IndexedDB/outbox rules, cached shells, selected locale and conditional cold reload.","F26-01"),
 (17,"Usability and device compatibility","Responsive journeys, shared-tablet onboarding, mobile/tablet browser projects, eight retained screenshot inspections and recovery controls.","F26-01,02,04,05,08,09; browser limits below"),
 (18,"Accessibility","Configured route-level axe tests plus independent offline-state landmark scan.","F26-07; no complete manual assistive-technology certification"),
 (19,"Localization and time handling","English/Telugu loading and persistence, formatting, UTC-naive instants and farm-local date boundaries.","F26-02,03,06; D26-04"),
 (20,"Performance and capacity","Bounded SQL/query surfaces, simulation/password admission, cancellation leases, capacity controls and cold-route JavaScript budgets.","Synthetic capacity controls passed; no target production scale supplied"),
 (21,"Background jobs and external services","Worker cycles, notification/outbox settlement, retries, budget reservations and maintenance.","O26-01,02; providers/storage doubled, not paid/live"),
 (22,"Deployment and configuration","Docker/Compose settings separation, secrets/proxy/TLS checks, real build attempts and static validation.","O26-01,07,08,09"),
 (23,"Backup and disaster recovery","Archive/manifest binding, metadata authentication, freshness and restore guards.","O26-04; whole-system restore/RPO/RTO not proven"),
 (24,"Monitoring and operational readiness","Private metrics, cross-process visibility, heartbeat, security ledger and freshness alerts.","O26-01,02,04; S26-02"),
 (25,"Dependencies, CI and release integrity","Fresh dependency/container checks, patch/build inputs, release preflight/cleanup, signatures and evidence gates.","O26-03,05,06,07,08,09"),
 (26,"Test quality and audit evidence","Fresh suite receipts, unchanged gates, new independent probes, mutation/SARIF validator adversarial inputs.","O26-05,06; this report retains failures and incomplete evidence"),
]

out = [
 "# Independent 26-track project audit",
 "",
 f"**Project:** Herdly / goat-farm-management-main  \n**Source revision:** `{manifest['commit']}`  \n**Audit date:** 4 October 2026 (America/Phoenix)  \n**Scope:** all 26 requested tracks; source review plus the explicitly recorded isolated runtime checks.  \n**Status:** audit findings documented; application fixes were not requested or implemented.",
 "",
 f"## Result\n\n**{len(issues)} distinct confirmed issues:** {counts['Critical']} Critical, {counts['High']} High, {counts['Medium']} Medium and {counts['Low']} Low. IDs are stable; an issue mapped to multiple tracks is counted only once. Severity is based on the documented trigger, prerequisites and demonstrated impact, not merely on a tool label.",
 "",
 "The highest-priority application failure is the production screening worker's use of API-only settings while recording provider telemetry. Its full-cycle reproduction loses valid provider results and exhausts the image's attempt budget. Other confirmed issues affect forecast calibration/accounting, reviewable screening evidence, offline Telugu availability, deployment/release safety and the integrity of audit evidence. The index and complete details below include every confirmed issue.",
 "",
 "Passing existing tests is not treated as proof that the application has no defects. Conversely, a validation gap is not promoted to a vulnerability. The report does not claim exhaustive discovery of every possible bug, production certification, veterinary accuracy, regulatory compliance or a complete external penetration test.",
 "",
 "## Independence, scope and evidence rules\n\nThree fresh reviewers worked independently on domain/database, frontend, and operations/testing scopes. They were instructed not to consult earlier audit conclusions. The coordinating reviewer covered authentication, authorization, privacy and contracts and ran shared validation. The earlier planning conversation had identified historical reports; those historical results were not imported as present-day findings or test passes. Cross-review happened after primary findings were written. New claims required current source locations and a concrete failure mode; all listed findings have fresh execution evidence at the level specified in their details.",
 "",
 f"The starting worktree was clean at the revision above. The manifest records {manifest['tracked_file_count']} tracked paths and hashes {manifest['hashed_current_files']} current tracked files outside historical audit/archive bundles. Review was a repository-wide inventory with focused source inspection across every subsystem, not a claim that every line of all generated clients and historical reports was manually reviewed. No application or existing test source was changed. New probes and reports live only in this new audit directory.",
 "",
 "Database probes used named disposable databases with explicit application and migration targets. Browser checks used owned isolated servers and a disposable database, with automatic reuse of unrelated running servers disabled. Provider, SMS, object-store and registry-delete reproductions used local doubles unless an individual receipt explicitly says otherwise. No real messages were sent, no production data was changed, and no release was published. Any synthetic build-input changes were confined to disposable audit contexts.",
 "",
 "Eight complementary methods were used: manual review; static/security scanning; real-DB and browser tests; adversarial/boundary probes; review and adversarial testing of mutation evidence; synthetic capacity/concurrency controls; controlled failure injection; and specialist/device validation-gap assessment. A new full-project mutation campaign and real farm/veterinary field trial were not run.",
 "",
 "## All 26 audit tracks",
 "",
 "| # | Track | Work performed | Findings / boundary |",
 "| --- | --- | --- | --- |",
]
out += [f"| {n} | {name} | {work} | {result} |" for n,name,work,result in tracks]
out += ["", "## Fresh validation", "", "[Coordinating commands and isolated browser settings](evidence/root/baseline-commands.md) · [Host/runtime versions](evidence/root/validation-environment.json).", "", "| Check | Actual outcome | Evidence |", "| --- | --- | --- |"]
out += [f"| {r['check']} | {r['outcome']} | [{r['label']}]({r['evidence']}) |" for r in validation['checks']]
out += ["", validation['interpretation'], "", "## Findings index", "", "| ID | Severity | Issue |", "| --- | --- | --- |"]
out += [f"| [{i['id']}](#{i['id'].lower()}) | {i['severity']} | {i['title']} |" for i in issues]
out += ["", "## Evidence limits and outstanding external validation", "",
 "- **Clinical/model validity:** synthetic provider responses and mathematical controls establish software behavior, not disease sensitivity/specificity or forecast agreement with an actual farm. Representative veterinarian-labeled photos, independent farm outcomes and policy assumptions still need specialist validation.",
 "- **Real services:** no paid AI/SMS calls, real object-store deletion, production credentials, hosted release execution or production deployment were performed. Release cleanup was reproduced against a fake registry command, not GitHub.",
 "- **Capacity:** no operating-scale targets were supplied in response to the optional audit question. Admission/concurrency controls and query structure were checked, but production p95 latency, soak stability and capacity at a stated herd/farm/user population remain unmeasured. Concurrent audit workloads also make incidental local timings unsuitable as production SLOs.",
 "- **Recovery and operations:** no coordinated database + versioned objects + key-escrow disaster-recovery drill or live alert-receiver acceptance test was completed. Local cryptographic/freshness probes are explicitly narrower than a whole-system restore.",
 "- **Devices and language:** browser emulation and controlled cache removal do not replace physical tablet sleep/restart/storage-pressure testing, camera testing, manual screen-reader testing or fluent Telugu review.",
 "- **Coverage breadth:** fresh upgrades and selected migration/constraint controls do not cover every historical populated upgrade/downgrade. Existing suite and sampled adversarial tests do not constitute a full new mutation campaign or proof of all possible interleavings.",
 "- **Browser reliability:** failed/interrupted browser runs remain failed/incomplete in the validation table. A later focused pass does not convert a failed full run into a clean full-browser verdict.",
 "", "## Recommended repair order", "",
 "1. Correct production screening configuration/telemetry and any reproduced clean-build blockers; verify the actual deployed worker/build environment, not just API-process unit tests.",
 "2. Correct financial/calibration transformations and preserve all flagged screening concerns through parsing and human review.",
 "3. Repair worker offline asset retention, first-use locale selection and upload/finish serialization; verify restart/offline behavior after browser HTTP-cache loss.",
 "4. Authenticate recovery manifests and constrain release cleanup to objects created by the current attempt; make worker monitoring observable across processes.",
 "5. Close smaller chronology, navigation, localization, accessibility and audit-ledger gaps; harden evidence gates and reproduce each original failure as a regression.",
 "", "## Complete finding details and primary review records", "",
 "The following records are included in full so this single Markdown file contains every issue, reproduction, impact, recommendation and stated limitation. File/line references are relative to the repository root. Raw logs and executable probes remain in the linked evidence directory. Prior successful probe iterations are not added to final unique test counts.",
]
for source in sources:
    body=(BASE/source).read_text()
    body=re.sub(r"^(#{2,3}) ([DFOS]26-\d+) —",lambda m:f'<a id="{m[2].lower()}"></a>\n\n{m[1]} {m[2]} —',body,flags=re.M)
    body=re.sub(r"^(#{1,5}) ",lambda m:"#"+m[0],body,flags=re.M)
    out += ["", "---", "", body]
out += ["", "## Independent challenge records", "",
 "- [Domain findings challenged by operations reviewer](peer-review-domain.md).",
 "- [Operations findings challenged by domain reviewer](peer-review-ops.md).",
 "- [Frontend findings challenged by coordinating reviewer](peer-review-frontend.md).",
 "- [Security findings challenged by frontend reviewer](peer-review-security.md).",
 "- [Consolidated report completeness and evidence review](peer-review-consolidated.md).",
 "", "## Source integrity and cleanup", "", validation['cleanup'], "",
 "[Source manifest](evidence/source-manifest.json) · [Validation summary](evidence/validation-summary.json) · [Machine-readable finding index](evidence/findings.json)", ""]
(BASE / "INDEPENDENT_26_TRACK_AUDIT.md").write_text("\n".join(out))
(BASE / "evidence/findings.json").write_text(json.dumps({"commit":manifest['commit'],"counts":dict(counts),"total":len(issues),"findings":issues},indent=2)+"\n")
print(json.dumps({"report":str(BASE / "INDEPENDENT_26_TRACK_AUDIT.md"),"issues":len(issues),"severities":dict(counts)}))
