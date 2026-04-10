#!/usr/bin/env python3
"""
Decrypt a Fernet token stored in bank_details.fermat_key (full ciphertext).

**Easiest (macOS / Homebrew Python, no global pip):** use the helper — it creates
``scripts/.decrypt-venv/`` and installs ``cryptography`` automatically::

  cd backend
  chmod +x scripts/run_decrypt_bank_token.sh   # once
  ./scripts/run_decrypt_bank_token.sh 'gAAAAA...'

Or with a venv that already has ``cryptography``::

  PYTHONPATH=. python3 scripts/decrypt_bank_token.py 'gAAAAA...'

File token::

  ./scripts/run_decrypt_bank_token.sh --file /tmp/token.txt
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _load_bank_key_from_dotenv() -> str:
    env_path = _BACKEND_ROOT / ".env"
    if not env_path.is_file():
        return (os.environ.get("BANK_ENCRYPTION_KEY") or "").strip()
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("BANK_ENCRYPTION_KEY="):
            val = line.split("=", 1)[1].strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            return val
    return (os.environ.get("BANK_ENCRYPTION_KEY") or "").strip()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--file":
        if len(sys.argv) < 3:
            print("Missing path after --file", file=sys.stderr)
            sys.exit(1)
        token = Path(sys.argv[2]).read_text().strip()
    else:
        token = sys.argv[1].strip()

    key = _load_bank_key_from_dotenv()
    if not key:
        print("BANK_ENCRYPTION_KEY not found in .env or environment.", file=sys.stderr)
        sys.exit(1)
    os.environ["BANK_ENCRYPTION_KEY"] = key

    from app.bank_crypto import decrypt_bank_payload

    out = decrypt_bank_payload(token)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
