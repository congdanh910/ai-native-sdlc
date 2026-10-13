#!/usr/bin/env bash
set -euo pipefail

for command in docker uv ssh codex claude gh rg; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Missing command: %s\n' "$command" >&2
    exit 1
  fi
done

if [[ ! -f .env ]]; then
  printf 'Copy .env.example to .env and set local values first.\n' >&2
  exit 1
fi

if rg -q 'CHANGE_ME' .env; then
  printf 'Replace all CHANGE_ME values in .env.\n' >&2
  exit 1
fi

python3 - <<'PY'
import base64
from pathlib import Path

values = dict(
    line.split("=", 1) for line in Path(".env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
key = values.get("LANGFLOW_SECRET_KEY", "")
try:
    decoded = base64.b64decode(key, altchars=b"-_", validate=True)
except (ValueError, base64.binascii.Error):
    decoded = b""
if len(decoded) != 32:
    raise SystemExit("LANGFLOW_SECRET_KEY must be a 32-byte URL-safe base64 Fernet key.")
PY

docker compose --env-file .env config --quiet
printf 'Local configuration checks passed. No containers were started.\n'
