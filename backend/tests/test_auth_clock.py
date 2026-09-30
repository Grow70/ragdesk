"""A clock correction must not be 'fixed' by disabling JWT time validation."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from app.services.auth import create_access_token, verified_user_id


def test_controlled_clock_reproduces_future_iat_and_still_rejects_expiration(
    monkeypatch,
):
    issued = datetime(2026, 9, 30, tzinfo=timezone.utc)
    current = issued - timedelta(seconds=2)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    user = uuid4()
    secret = "controlled-clock-test-secret-at-least-32-bytes"
    token = create_access_token(user, secret, 30, issued_at=issued)
    monkeypatch.setattr(jwt.api_jwt, "datetime", Clock)
    with pytest.raises(jwt.ImmatureSignatureError):
        verified_user_id(token, secret)
    current = issued
    assert verified_user_id(token, secret) == user
    current = issued + timedelta(minutes=30)
    with pytest.raises(jwt.ExpiredSignatureError):
        verified_user_id(token, secret)
