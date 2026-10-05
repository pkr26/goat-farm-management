"""One bounded full AMD64 build with an observational epoll_ctl interposer.

Run probe_minimal_next_build.py controls first. Compile epoll_diagnostic.c in
an isolated matching musl/AMD64 container; copy the resulting shared object
to /tmp/goatfarm-fix29-epoll-diagnostic.so. The app and deps come from exactly
the intermediate image used by the two failed builds. No app source changes.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
NAME = "goatfarm-fix29-instrumented-next-amd64"
IMAGE = "be94e1d95837"
LIBRARY = Path("/tmp/goatfarm-fix29-epoll-diagnostic.so")
script = "corepack enable && exec env LD_PRELOAD=/tmp/epoll-diagnostic.so pnpm build"
result = {"image": IMAGE, "platform": "linux/amd64", "timeout_seconds": 480, "source_modified": False, "interposer_sha256": hashlib.sha256(LIBRARY.read_bytes()).hexdigest(), "command": script}
started = time.monotonic()
try:
    created = subprocess.run(["docker", "create", "--name", NAME, "--platform", "linux/amd64", "--ulimit", "core=0", IMAGE, "sh", "-c", script], check=True, capture_output=True, text=True)
    result["container_id"] = created.stdout.strip()
    subprocess.run(["docker", "cp", str(LIBRARY), NAME + ":/tmp/epoll-diagnostic.so"], check=True, capture_output=True)
    with (ROOT / "instrumented-next-amd64.log").open("w") as log:
        completed = subprocess.run(["docker", "start", "--attach", NAME], stdout=log, stderr=subprocess.STDOUT, timeout=480, check=False)
    result["docker_attach_exit_code"] = completed.returncode
except subprocess.TimeoutExpired:
    result["timed_out"] = True
finally:
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    inspected = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", NAME], capture_output=True, text=True, check=False)
    result["container_state"] = json.loads(inspected.stdout) if inspected.returncode == 0 else inspected.stderr
    top = subprocess.run(["docker", "top", NAME, "-eo", "pid,ppid,etime,pcpu,comm,args"], capture_output=True, text=True, check=False)
    result["remaining_processes"] = top.stdout.strip() if top.returncode == 0 else "none"
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True, check=False)
    (ROOT / "instrumented-next-amd64.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
