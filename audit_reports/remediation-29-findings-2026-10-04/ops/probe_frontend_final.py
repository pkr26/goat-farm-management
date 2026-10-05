"""Start final standalone images and exercise health plus actual native sharp."""
from pathlib import Path
import json
import subprocess
import time

OUT = Path(__file__).resolve().parent
DOCKER = str(Path.home() / ".local/bin/docker")
records = []


def command(args, timeout=60):
    result = subprocess.run([DOCKER, *args], capture_output=True, text=True, timeout=timeout)
    return {"command": ["docker", *args], "exit": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


for arch, ref in [("arm64", "goatfarm-fix29-frontend:104-final"),
                  ("amd64", "goatfarm-fix29-frontend:104-final-amd64")]:
    info = json.loads(subprocess.check_output([DOCKER, "image", "inspect", ref], text=True))[0]
    assert info["Architecture"] == arch
    container = f"goatfarm-fix29-frontend-final-smoke-{arch}"
    record = {"arch": arch, "ref": ref, "image_id": info["Id"]}
    record["launch"] = command(["run", "-d", "--name", container, "--network", "none",
                                "--ulimit", "core=0", "--platform", "linux/" + arch, ref])
    assert record["launch"]["exit"] == 0
    try:
        for attempt in range(30):
            record["health"] = command([
                "exec", container, "node", "-e",
                'fetch("http://127.0.0.1:3000/healthz").then(async r=>{console.log(r.status,await r.text());process.exit(r.ok?0:1)}).catch(()=>process.exit(1))',
            ])
            if record["health"]["exit"] == 0:
                break
            time.sleep(1)
        record["native_versions"] = command([
            "exec", container, "node", "-e", 'console.log(JSON.stringify(require("sharp").versions))',
        ])
        assert record["native_versions"]["exit"] == 0
        record["native_versions_parsed"] = json.loads(record["native_versions"]["stdout"])
        assert record["native_versions_parsed"]["sharp"]
        assert record["native_versions_parsed"]["vips"]
        record["logs"] = command(["logs", container])
    finally:
        record["cleanup"] = command(["rm", "-f", "-v", container])
        records.append(record)
        (OUT / "frontend-final-runtime.json").write_text(json.dumps(records, indent=2) + "\n")
    assert record["health"]["exit"] == 0
    assert record["cleanup"]["exit"] == 0
    print(arch, "standalone health and native sharp passed", flush=True)
