"""
Encrypt / decrypt campaign bank details at rest using Fernet (symmetric).

Set BANK_ENCRYPTION_KEY in the environment to a Fernet key:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

The same key must be used to decrypt when processing payouts.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = (os.getenv("BANK_ENCRYPTION_KEY") or "").strip()
    if not key:
        raise RuntimeError("BANK_ENCRYPTION_KEY is not set")
    return Fernet(key.encode("ascii"))


def encrypt_bank_payload(payload: dict[str, Any]) -> str:
    """JSON-encode payload and return an ASCII Fernet token."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_bank_payload(token: str) -> dict[str, Any]:
    """Decrypt a token produced by encrypt_bank_payload."""
    try:
        raw = _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as e:
        raise ValueError("Invalid ciphertext or wrong BANK_ENCRYPTION_KEY") from e
    return json.loads(raw.decode("utf-8"))
