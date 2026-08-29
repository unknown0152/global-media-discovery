#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src"

python3 -m compileall -q "$ROOT/src" "$ROOT/tests"
python3 -m py_compile "$ROOT/scripts/build-seed.py"
python3 -m unittest discover -s "$ROOT/tests" -v
python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
long_lines = []
for base in (root / "src", root / "tests", root / "scripts"):
    for path in sorted(base.rglob("*.py")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            if len(line) > 99:
                long_lines.append(f"{path.relative_to(root)}:{number}:{len(line)}")
if long_lines:
    raise SystemExit("Python lines exceed 99 characters:\n" + "\n".join(long_lines))
for suffix in ("-wal", "-shm"):
    sidecar = root / f"seed/catalog.sqlite3{suffix}"
    if sidecar.exists():
        raise SystemExit(f"bundled seed sidecar must not exist: {sidecar}")
PY
go test ./...
npm run typecheck
npm run build
bash -n \
  "$ROOT/scripts/install.sh" \
  "$ROOT/scripts/build-installer.sh" \
  "$ROOT/scripts/build-release.sh" \
  "$ROOT/scripts/test.sh" \
  "$ROOT/bin/gmd"
docker compose -f "$ROOT/compose.yaml" config --format json |
python3 -c '
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
assert sorted(services) == ["api", "caddy", "collector"]
for name in services:
    assert services[name]["read_only"], f"{name} root filesystem is writable"
assert list(services["api"]["networks"]) == ["backend"]
assert list(services["collector"]["networks"]) == ["source_egress"]
assert config["networks"]["backend"]["internal"]
assert services["api"]["cap_drop"] == ["ALL"]
assert services["collector"]["cap_drop"] == ["ALL"]
assert services["caddy"]["user"] == "1000:1000"
'

echo "All validation gates passed."
