#!/usr/bin/env bash
#
# The runtime image, the Dockerfile's last stage and the one Render builds,
# built on its own and checked (items 8 and 9, step 14):
#
#   USER     it runs as a user that is not root
#   PIPCHECK pip check finds nothing broken
#   NOTEST   pytest, and the four packages only pytest needs, are absent
#   IMPORT   every module under app/ imports
#
# Exits 0 when all four pass. No secret and no environment file: nothing
# here connects to anything.
set -euo pipefail

cd "$(dirname "$0")/../backend"

IMAGE=cartograph-backend-runtime
docker build --quiet --target runtime -t "$IMAGE" . > /dev/null

fail=0
run() { docker run --rm --network none "$IMAGE" "$@"; }

uid="$(run id -u)"
if [[ "$uid" != "0" ]]; then echo "USER PASS  uid $uid ($(run id -un))"; else echo "USER FAIL  root"; fail=1; fi

if out="$(run pip check 2>&1)"; then echo "PIPCHECK PASS  $out"; else echo "PIPCHECK FAIL  $out"; fail=1; fi

present="$(run python -c '
import importlib.util
names = ["pytest", "_pytest", "iniconfig", "pluggy", "packaging", "pygments"]
print(" ".join(n for n in names if importlib.util.find_spec(n) is not None))
')"
if [[ -z "$present" ]]; then echo "NOTEST PASS  no pytest, iniconfig, pluggy, packaging or pygments"; else echo "NOTEST FAIL  present: $present"; fail=1; fi

if out="$(run python -c '
import importlib, pkgutil
import app
names = sorted(m.name for m in pkgutil.walk_packages(app.__path__, "app."))
failed = []
for name in names:
    try:
        importlib.import_module(name)
    except Exception as error:
        failed.append(f"{name}: {type(error).__name__}: {error}")
print(f"{len(names)} modules, {len(failed)} failed")
for line in failed:
    print(line)
raise SystemExit(1 if failed else 0)
' 2>&1)"; then echo "IMPORT PASS  $out"; else echo "IMPORT FAIL  $out"; fail=1; fi

exit "$fail"
