"""Use real GPG signing/encryption and an owned temporary keyring; no DB or restore."""
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend/scripts"))
from recovery_inventory import REQUIRED_KEY_MATERIAL

IMAGE = "ruby:3.3@sha256:8c53fba677325dcb3666a9ac8ad710d21356cc61753faf3db5520e40a36abe07"
with tempfile.TemporaryDirectory(prefix="goatfarm-a26-gpg-", dir=pathlib.Path.home() / ".cache") as raw:
    tmp = pathlib.Path(raw)
    (tmp / "keyring").mkdir(mode=0o700)
    (tmp / "backups").mkdir()
    created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)
    epoch = str(int(created.timestamp()))
    payload = tmp / "fixture.dump"
    payload.write_bytes(b"Audit-owned payload; tests cryptographic envelope, not PostgreSQL archive restoration.\n")
    archive = tmp / "backups" / f"goatfarm-{created:%Y-%m-%dT%H-%M-%SZ}-signedfixture.dump.gpg"
    base = ["docker", "run", "--rm", "--network", "none", "-v", f"{tmp}:/work", "--entrypoint", "gpg", IMAGE,
            "--homedir", "/work/keyring", "--batch", "--yes"]
    def gpg(args):
        done = subprocess.run(base + args, capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr)
        return done
    gpg(["--faked-system-time", epoch, "--pinentry-mode", "loopback", "--passphrase", "", "--quick-generate-key",
         "Audit Temporary <backup-audit@example.invalid>", "rsa2048", "sign,encr", "0"])
    fingerprint = next(line.split(":")[9] for line in gpg(["--with-colons", "--list-keys"]).stdout.splitlines() if line.startswith("fpr:"))
    gpg(["--faked-system-time", epoch, "--local-user", fingerprint, "--recipient", fingerprint,
         "--sign", "--encrypt", "--output", "/work/backups/" + archive.name, "/work/fixture.dump"])
    def verify():
        done = gpg(["--status-fd", "1", "--output", "/work/verified.dump", "--decrypt", "/work/backups/" + archive.name])
        assert (tmp / "verified.dump").read_bytes() == payload.read_bytes()
        assert "[GNUPG:] VALIDSIG " in done.stdout
        return [line for line in done.stdout.splitlines() if "GOODSIG" in line or "VALIDSIG" in line]
    valid_before = verify()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(digest + "  " + archive.name + "\n")
    inventory = {"schema_version": 1, "generated_at": created.isoformat(),
                 "screening_objects": {"bucket": "audit-bucket", "prefix": "raw", "versioning": "Enabled",
                                       "recovery_point": "unchanged-72h-old-recovery-point", "restore_receipt": "unchanged-old-receipt",
                                       "manifest": {"name": "objects.json", "bytes": 1, "sha256": "a" * 64}},
                 "key_material": {name: {"sha256": "b" * 64, "escrow_receipt": "synthetic-offline-receipt"} for name in REQUIRED_KEY_MATERIAL},
                 "database_artifact": {"name": archive.name, "sha256": digest}}
    sidecar = archive.with_name(archive.name + ".recovery.json")
    sidecar.write_text(json.dumps(inventory))
    command = [sys.executable, str(ROOT / "backend/scripts/check_backup_freshness.py"), str(archive.parent), "--max-age-hours", "26"]
    before = subprocess.run(command, capture_output=True, text=True)
    inventory["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sidecar.write_text(json.dumps(inventory))
    after = subprocess.run(command, capture_output=True, text=True)
    valid_after = verify()
    print(json.dumps({"gpg_image": IMAGE, "fingerprint": fingerprint, "signed_at_utc": created.isoformat(),
                      "gpg_signature_before": valid_before, "gpg_signature_after": valid_after,
                      "same_valid_signature": valid_before == valid_after,
                      "before_exit": before.returncode, "before_stderr": before.stderr,
                      "after_exit": after.returncode, "after_stdout": after.stdout,
                      "archive_sha256": digest, "archive_bytes_unchanged": hashlib.sha256(archive.read_bytes()).hexdigest() == digest,
                      "only_changed_field": "unsigned recovery.json generated_at",
                      "scope": "Real 72-hour-old GPG signature+encryption over fixture payload; not a PostgreSQL restore or production key."}, indent=2))
