#!/usr/bin/env bash
# Decrypt bank_details ciphertext without relying on system pip (PEP 668 on Homebrew Python).
# Creates a tiny venv under scripts/.decrypt-venv/ on first run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MINI="${ROOT}/scripts/.decrypt-venv"
if [[ ! -x "${MINI}/bin/python3" ]]; then
  echo "Creating ${MINI} (one-time)..." >&2
  python3 -m venv "${MINI}"
fi
if ! "${MINI}/bin/python3" -c "import cryptography" 2>/dev/null; then
  "${MINI}/bin/pip" install -q "cryptography>=42.0.0"
fi
export PYTHONPATH="${ROOT}"
exec "${MINI}/bin/python3" "${ROOT}/scripts/decrypt_bank_token.py" "$@"
