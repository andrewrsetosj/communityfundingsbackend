#!/usr/bin/env python3
"""
Load backend/.env and verify AWS keys with STS (no secrets printed).

Usage (from repo):
  cd backend && source .venv/bin/activate && python scripts/verify_aws_credentials.py
"""
from __future__ import annotations

import os
import sys

# backend/ as cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
except ImportError:
    print("Install python-dotenv: pip install python-dotenv")
    sys.exit(1)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

key_id = (os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
secret = (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
region = (os.getenv("AWS_REGION") or "us-east-1").strip()
token = (os.getenv("AWS_SESSION_TOKEN") or "").strip() or None

print("AWS_ACCESS_KEY_ID length:", len(key_id), "| prefix:", repr(key_id[:4]) if key_id else "empty")
print("AWS_SECRET_ACCESS_KEY length:", len(secret), "(expect 40 chars for standard secret)")
print("AWS_REGION:", region)
print("AWS_SESSION_TOKEN set:", bool(token), "(required for temporary ASIA... keys)")
print()

if not key_id or not secret:
    print("FAIL: Missing AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY after .strip()")
    sys.exit(1)

if len(key_id) != 20:
    print("WARN: Access key id is usually 20 characters (AKIA... or ASIA...)")

try:
    import boto3

    kwargs = dict(
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name=region,
    )
    if token:
        kwargs["aws_session_token"] = token

    sts = boto3.client("sts", **kwargs)
    ident = sts.get_caller_identity()
    print("OK — get_caller_identity:")
    print("  Account:", ident.get("Account"))
    print("  Arn:", ident.get("Arn"))
    print("  UserId:", ident.get("UserId"))
except Exception as e:
    print("FAIL:", e)
    sys.exit(2)
