"""Minimal Next build under the exact failed builder dependency image.

No application UI is imported. Guard variants import the repository's actual
safety guard. Container changes are disposable. This is a bounded diagnostic;
a pass does not prove the full project or image succeeds.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--pnpm", action="store_true")
parser.add_argument("--guard", action="store_true")
parser.add_argument("--isolate-sharp", action="store_true")
args = parser.parse_args()
suffix = ("-pnpm" if args.pnpm else "") + ("-guard" if args.guard else "") + ("-isolated" if args.isolate_sharp else "")
NAME = "goatfarm-fix29-minimal-next-amd64" + suffix
IMAGE = "be94e1d95837"
script = r"""
set -eu
mkdir -p /app/probe/app
cat > /app/probe/package.json <<'EOF'
{"name":"minimal-next-teardown-probe","private":true,"scripts":{"build":"next build"},"dependencies":{"next":"16.3.8","react":"19.2.8","react-dom":"19.2.8"}}
EOF
ln -s /app/node_modules /app/probe/node_modules
cat > /app/probe/next.config.mjs <<'EOF'
export default { output: "standalone", turbopack: {root: "/app"} };
EOF
cat > /app/probe/app/layout.jsx <<'EOF'
export default function RootLayout({children}) { return <html lang="en"><body>{children}</body></html> }
EOF
cat > /app/probe/app/page.jsx <<'EOF'
export default function Page() { return <main><h1>Minimal lifecycle probe</h1></main> }
EOF
cd /app/probe
node -p 'JSON.stringify({versions:process.versions,next:require("next/package.json").version})'
exec node /app/node_modules/next/dist/bin/next build
"""
if args.guard:
    script = script.replace("next.config.mjs", "next.config.ts").replace(
        'export default { output: "standalone", turbopack: {root: "/app"} };',
        'import { assertImageDecodeSafetyForBuild } from "../src/lib/image-deps-guard";\n'
        'export default function config(phase: string) { assertImageDecodeSafetyForBuild(phase); return { output: "standalone", turbopack: {root: "/app"} }; }'
    )
if args.isolate_sharp:
    if not args.guard:
        parser.error("--isolate-sharp requires --guard")
    script = script.replace(
        'import { assertImageDecodeSafetyForBuild }',
        'import { spawnSync } from "node:child_process";\nimport { assertImageDecodeSafetyForBuild }',
    ).replace(
        'assertImageDecodeSafetyForBuild(phase);',
        'const probeCode = String.raw`process.stdout.write(JSON.stringify(require("sharp").versions))`; const probe = spawnSync(process.execPath, ["-e", probeCode], {encoding: "utf8", timeout: 10000}); '
        'if (probe.error || probe.status !== 0) throw new Error("isolated sharp probe failed"); '
        'assertImageDecodeSafetyForBuild(phase, {sharpProbe: JSON.parse(probe.stdout)});',
    )
if args.pnpm:
    script = script.replace("exec node /app/node_modules/next/dist/bin/next build", "corepack enable\nexec pnpm build")
command = ["docker", "run", "--name", NAME, "--platform", "linux/amd64", "--ulimit", "core=0"]
if not args.pnpm:
    command += ["--env", "NEXT_TELEMETRY_DISABLED=1"]
command += [IMAGE, "sh", "-c", script]
started = time.monotonic()
result = {"image": IMAGE, "platform": "linux/amd64", "timeout_seconds": 240, "application_ui_imported": False, "safety_guard_imported": args.guard, "sharp_probe_in_subprocess": args.isolate_sharp, "bundler": "turbopack", "pnpm_wrapper": args.pnpm, "telemetry_disabled": not args.pnpm}
try:
    with (ROOT / f"minimal-next-amd64{suffix}.log").open("w") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=240, check=False)
    result["exit_code"] = completed.returncode
except subprocess.TimeoutExpired:
    result["timed_out"] = True
finally:
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    inspected = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", NAME], capture_output=True, text=True, check=False)
    result["container_state"] = json.loads(inspected.stdout) if inspected.returncode == 0 else inspected.stderr
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True, check=False)
    (ROOT / f"minimal-next-amd64{suffix}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
