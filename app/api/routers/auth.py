from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.security import (
    create_user_session,
    delete_session,
    hash_password,
    now_iso,
    verify_password,
)
from app.db.models import TrackedClub, User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TrackClubRequest(BaseModel):
    club_id: str
    platform: str = "common-gen5"
    club_name: str | None = None


class UserResponse(BaseModel):
    id: int
    email: str
    plan_tier: str
    created_at: str
    tracked_clubs: list[dict[str, object]]


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    data: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email_clean = data.email.strip().lower()
    if "@" not in email_clean or "." not in email_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe um endereço de e-mail válido.",
        )

    existing = db.query(User).filter(User.email == email_clean).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este e-mail já está cadastrado.",
        )

    if len(data.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A senha deve ter pelo menos 6 caracteres.",
        )

    salt, pw_hash = hash_password(data.password)
    now_str = now_iso()

    user = User(
        email=email_clean,
        password_hash=pw_hash,
        salt=salt,
        plan_tier="free",
        created_at=now_str,
        updated_at=now_str,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_user_session(db, user.id)
    response.set_cookie(
        key="fc27_session",
        value=token,
        httponly=True,
        max_age=30 * 86400,
        samesite="lax",
    )

    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "plan_tier": user.plan_tier,
        },
    }


@router.post("/login")
def login(
    data: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email_clean = data.email.strip().lower()
    user = db.query(User).filter(User.email == email_clean).first()
    if not user or not verify_password(data.password, user.salt, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos.",
        )

    token = create_user_session(db, user.id)
    response.set_cookie(
        key="fc27_session",
        value=token,
        httponly=True,
        max_age=30 * 86400,
        samesite="lax",
    )

    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "plan_tier": user.plan_tier,
        },
    }


@router.post("/logout")
def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    for sess in user.sessions:
        delete_session(db, sess.token)
    response.delete_cookie(key="fc27_session")
    return {"ok": True, "message": "Sessão encerrada com sucesso."}


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserResponse:
    tracked_list = db.query(TrackedClub).filter(TrackedClub.user_id == user.id).all()
    clubs = [
        {
            "id": tc.id,
            "club_id": tc.club_id,
            "platform": tc.platform,
            "club_name": tc.club_name,
            "is_active": bool(tc.is_active),
            "created_at": tc.created_at,
        }
        for tc in tracked_list
    ]
    return UserResponse(
        id=user.id,
        email=user.email,
        plan_tier=user.plan_tier,
        created_at=user.created_at,
        tracked_clubs=clubs,
    )


@router.post("/track-club", status_code=status.HTTP_201_CREATED)
def track_club(
    data: TrackClubRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    club_id_clean = data.club_id.strip()
    existing = (
        db.query(TrackedClub)
        .filter(TrackedClub.user_id == user.id, TrackedClub.club_id == club_id_clean)
        .first()
    )
    if existing:
        existing.is_active = 1
        db.commit()
        return {"ok": True, "tracked_club_id": existing.id, "message": "Clube reativado."}

    tc = TrackedClub(
        user_id=user.id,
        club_id=club_id_clean,
        platform=data.platform,
        club_name=data.club_name,
        is_active=1,
        created_at=now_iso(),
    )
    db.add(tc)
    db.commit()
    db.refresh(tc)
    return {"ok": True, "tracked_club_id": tc.id, "message": "Clube adicionado para monitoramento."}


@router.delete("/track-club/{club_id}")
def untrack_club(
    club_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    tc = (
        db.query(TrackedClub)
        .filter(TrackedClub.user_id == user.id, TrackedClub.club_id == club_id.strip())
        .first()
    )
    if not tc:
        raise HTTPException(status_code=404, detail="Clube não encontrado na sua lista.")

    db.delete(tc)
    db.commit()
    return {"ok": True, "message": "Clube removido do monitoramento."}
