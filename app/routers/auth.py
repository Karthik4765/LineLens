"""Registration and login endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.errors import APIError
from app.models import User
from app.schemas import TokenResponse, UserOut, UserLogin, UserRegister
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id),
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
def register(payload: UserRegister, db: Session = Depends(get_db)) -> TokenResponse:
    """Create a user and return a token so the client can sign in immediately."""

    email = str(payload.email).lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise APIError(409, "email_taken", "An account with that email already exists.")

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise APIError(409, "email_taken", "An account with that email already exists.") from exc
    db.refresh(user)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse, summary="Log in")
def login(payload: UserLogin, db: Session = Depends(get_db)) -> TokenResponse:
    """Verify credentials and return a signed JWT."""

    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Email or password is incorrect.")
    return _token_response(user)


@router.get("/me", response_model=UserOut, summary="Get the current user")
def me(user: User = Depends(get_current_user)) -> UserOut:
    """Return the account associated with the bearer token."""

    return UserOut.model_validate(user)
