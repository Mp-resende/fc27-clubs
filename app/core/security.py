from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import secrets

from sqlalchemy.orm import Session

from app.db.models import User, UserSession


def now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_password(password: str) -> tuple[str, str]:
    """Generate a cryptographic salt and hash a password using PBKDF2-HMAC-SHA256."""
    salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return salt, pw_hash


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    """Safely verify a password against stored salt and hash in constant time."""
    candidate_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return hmac.compare_digest(candidate_hash, password_hash)


def create_user_session(db: Session, user_id: int, days_valid: int = 30) -> str:
    """Create and persist a new session token for the user."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_dt = now + timedelta(days=days_valid)

    session = UserSession(
        token=token,
        user_id=user_id,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    db.add(session)
    db.commit()
    return token


def get_user_by_session(db: Session, token: str | None) -> User | None:
    """Return User associated with token if token is valid and not expired."""
    if not token:
        return None

    sess = db.get(UserSession, token)
    if not sess:
        return None

    now_str = now_iso()
    if sess.expires_at < now_str:
        db.delete(sess)
        db.commit()
        return None

    return sess.user


def delete_session(db: Session, token: str) -> None:
    """Revoke and delete a session token."""
    sess = db.get(UserSession, token)
    if sess:
        db.delete(sess)
        db.commit()
