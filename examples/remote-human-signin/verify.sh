#!/usr/bin/env bash
set -euo pipefail
bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
python3 - "$stage" <<'PY'
import hashlib
import io
import pathlib
import sys
import tarfile
import urllib.request

url = "https://files.pythonhosted.org/packages/ae/9f/d77a1d1076075c80ee638b4c355725be52cfde05becab09cb38d36234352/signin_window-0.4.4.tar.gz"
with urllib.request.urlopen(url) as response:
    data = response.read()
expected = "3a33210007eafbd8accacc55b6f75b43e06f6909fe40bc282ef6dcd054322b59"
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit("library source checksum mismatch")
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    archive.extractall(pathlib.Path(sys.argv[1]), filter="data")
PY
library="$stage/signin_window-0.4.4"
git -C "$library" apply --check "$bundle/library-prototype.patch"
git -C "$library" apply "$bundle/library-prototype.patch"
fetch_base() {
  git init -q "$stage/$1"
  git -C "$stage/$1" fetch -q --depth=1 "https://github.com/alexdlaird/$1.git" "$2"
  git -C "$stage/$1" checkout -q --detach FETCH_HEAD
}
fetch_base amazon-orders b6fdc880a8e2f54b7824a919d4bb91cc3d561bcf
fetch_base amazon-orders-mcp 78a0c814830c367fbcc3e24f8e4e36daeb005262
for patch in 01-browser-environment 02-verified-cookie-import; do
  git -C "$stage/amazon-orders" apply --check "$bundle/$patch.patch"
  git -C "$stage/amazon-orders" apply "$bundle/$patch.patch"
done
git -C "$stage/amazon-orders-mcp" apply --check "$bundle/03-serialized-sign-in-adapter.patch"
git -C "$stage/amazon-orders-mcp" apply "$bundle/03-serialized-sign-in-adapter.patch"
uv venv "$stage/venv"
uv pip install --python "$stage/venv/bin/python" "$library" "$stage/amazon-orders" \
  "$stage/amazon-orders-mcp" playwright pytest httpx uvicorn responses
"$stage/venv/bin/python" -m playwright install chromium
(cd "$stage/amazon-orders" && "$stage/venv/bin/python" -m pytest \
  tests/unit/test_import_cookies.py tests/unit/contrib/test_browser_environment.py -q)
if [[ ${VERIFY_OFFLINE:-0} == 1 ]]; then
  uv pip install --python "$stage/venv/bin/python" responses lxml mypy flake8 flake8-pyproject pep8-naming
  (cd "$stage/amazon-orders" && "$stage/venv/bin/python" -m pytest tests/unit -q)
  (cd "$stage/amazon-orders-mcp" && "$stage/venv/bin/python" -m pytest -q && \
    "$stage/venv/bin/python" -m mypy amazonorders_mcp && \
    "$stage/venv/bin/python" -m flake8 amazonorders_mcp tests)
fi
SIGNIN_XVFB=${SIGNIN_XVFB:-Xvfb} SIGNIN_X11VNC=${SIGNIN_X11VNC:-x11vnc} \
  NOVNC_DIR=${NOVNC_DIR:?set NOVNC_DIR to noVNC 1.6.0 assets} \
  "$stage/venv/bin/python" -m pytest "$library/examples/amazon/test_acceptance.py" -v
