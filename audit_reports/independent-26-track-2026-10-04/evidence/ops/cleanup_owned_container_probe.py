"""Remove only explicitly named artifacts created by this audit subtask."""
import json
import pathlib
import shutil
import subprocess

OUT = pathlib.Path(__file__).resolve().parent
events = []
for tag in ("goatfarm-a26-backend:7245368", "goatfarm-a26-edge:7245368"):
    done = subprocess.run(["docker", "image", "rm", "--no-prune", tag], capture_output=True, text=True)
    events.append({"action": "remove-owned-tag-and-unreferenced-leaf-image", "target": tag,
                   "exit": done.returncode, "output": done.stdout + done.stderr})
container = "f6ba7367658a"
check = subprocess.run(["docker", "container", "inspect", container], capture_output=True, text=True)
if check.returncode == 0:
    info = json.loads(check.stdout)[0]
    assert info["State"]["Running"] is False
    assert info["Config"]["Cmd"] == ["/bin/sh", "-c", "pnpm install --frozen-lockfile"]
    done = subprocess.run(["docker", "container", "rm", container], capture_output=True, text=True)
    events.append({"action": "remove-owned-stopped-failed-build-container", "target": container,
                   "exit": done.returncode, "output": done.stdout + done.stderr})
trivy = "aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
done = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", "/tmp:/audit-daemon-tmp",
                       "--entrypoint", "sh", trivy, "-c", "rm -rf /audit-daemon-tmp/goatfarm-a26-container && test ! -e /audit-daemon-tmp/goatfarm-a26-container"],
                      capture_output=True, text=True)
events.append({"action": "remove-owned-Colima-VM-temporary-cache", "target": "/tmp/goatfarm-a26-container",
               "exit": done.returncode, "output": done.stdout + done.stderr})
for target in (pathlib.Path.home() / ".cache/goatfarm-a26-container", pathlib.Path("/tmp/goatfarm-a26-container"),
               pathlib.Path("/tmp/goatfarm-a26-ops-pip-audit"), pathlib.Path("/tmp/goatfarm-a26-ops-requirements.txt")):
    existed = target.exists()
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    events.append({"action": "remove-owned-host-temporary-path", "target": str(target), "existed": existed, "absent_after": not target.exists()})
print(json.dumps({"events": events, "scope": "No global image/container/builder prune; existing bases/scanner/tool images and prior application images untouched. Docker build cache parent layers may remain."}, indent=2))
