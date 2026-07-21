"""Authentication service: password hashing, JWT management, user authentication."""

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mysql_models import User


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256 with random salt)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a plain-text password using PBKDF2-HMAC-SHA256 with a 16-byte salt.

    Returns a string in the format ``pbkdf2:sha256$<iterations>$<salt>$<hash>``.
    """
    salt = os.urandom(16)
    iterations = 600_000
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2:sha256${iterations}${salt.hex()}${pwd_hash.hex()}"


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plain-text password against a hash produced by :func:`hash_password`."""
    try:
        algo, iterations_str, salt_hex, hash_hex = password_hash.split("$")
        # parse algorithm portion (e.g. "pbkdf2:sha256")
        _, hash_func = algo.split(":")
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        computed = hashlib.pbkdf2_hmac(
            hash_func, plain_password.encode("utf-8"), salt, iterations
        )
        return computed == expected
    except (ValueError, AttributeError, IndexError):
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_access_token(user_id: int, tenant_id: int) -> str:
    """Create a short-lived JWT access token."""
    expire = _now() + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "type": "access",
        "iat": _now(),
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def create_refresh_token(user_id: int, tenant_id: int) -> str:
    """Create a long-lived JWT refresh token."""
    expire = _now() + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "type": "refresh",
        "iat": _now(),
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token.

    Returns the decoded payload dict on success, or ``None`` if the token is
    invalid, expired, or tampered with.
    """
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "type"]},
        )
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# User authentication
# ---------------------------------------------------------------------------

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Look up a user by *email* and verify *password*.

    Returns the :class:`User` instance on success, or ``None`` if the
    credentials are invalid or the user is inactive.
    """
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
