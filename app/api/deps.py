from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, settings as _settings
from app.core.security import get_user_by_session
from app.db.models import User
from app.db.session import get_db as _get_db


def get_db() -> Generator[Session, None, None]:
    """Yield a database session for use in FastAPI dependencies."""
    yield from _get_db()


def get_settings() -> Settings:
    """Return the application settings singleton."""
    return _settings


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """Extract user from session cookie or Authorization header, or None if anonymous."""
    token = request.cookies.get("fc27_session")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

    if not token:
        return None

    return get_user_by_session(db, token)


def get_current_user(
    user: User | None = Depends(get_optional_user),
) -> User:
    """Require an authenticated user; raises 401 otherwise."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária. Faça login para continuar.",
        )
    return user


def require_pro_user(
    user: User = Depends(get_current_user),
) -> User:
    """Require an active PRO subscription; raises 403 otherwise."""
    if user.plan_tier != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recurso exclusivo do Plano Pro. Faça upgrade para ter acesso.",
        )
    return user
