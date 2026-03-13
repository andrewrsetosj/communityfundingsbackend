# cf-backend/tests/test_jwt_utils.py
import os
import pytest
import jwt_utils
from jwt import InvalidTokenError

def test_verify_token_without_configuration(monkeypatch):
    # Ensure both are unset for the test
    monkeypatch.setenv("CLERK_JWKS_URI", "")
    monkeypatch.setenv("CLERK_PEM_PUBLIC_KEY", "")
    # reload module attributes if necessary
    import importlib
    importlib.reload(jwt_utils)

    with pytest.raises(InvalidTokenError):
        jwt_utils.verify_token("doesntmatter")
