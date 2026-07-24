from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.api.auth_dependencies import CurrentAuth, CurrentUser
from bond_trading.api.schemas import (
    ChangePasswordRequest,
    LoginOut,
    LoginRequest,
    UserOut,
)
from bond_trading.application.services.auth import AuthenticationError, AuthService
from bond_trading.core.config import get_settings
from bond_trading.domain.errors import DomainError
from bond_trading.infrastructure.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/login", response_model=LoginOut, summary="Create an authenticated session")
async def login(
    payload: LoginRequest, request: Request, response: Response, session: Session
) -> LoginOut:
    settings = get_settings().auth
    try:
        credentials = await AuthService(session, settings).authenticate(
            payload.login, payload.password, request.headers.get("user-agent")
        )
    except AuthenticationError as exc:
        raise HTTPException(401, exc.message) from exc
    response.set_cookie(
        settings.session_cookie_name,
        credentials.token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        credentials.csrf_token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return LoginOut(
        user=UserOut.model_validate(credentials.user),
        access_token=credentials.token,
        csrf_token=credentials.csrf_token,
    )


@router.get("/me", response_model=UserOut, summary="Get the current user")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/logout", status_code=204, summary="Revoke the current session")
async def logout(auth: CurrentAuth, response: Response, session: Session) -> Response:
    await AuthService(session, get_settings().auth).revoke(auth.session)
    response.delete_cookie(get_settings().auth.session_cookie_name, path="/")
    response.delete_cookie(get_settings().auth.csrf_cookie_name, path="/")
    response.status_code = 204
    return response


@router.post("/change-password", status_code=204, summary="Change the current password")
async def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    response: Response,
    session: Session,
) -> Response:
    try:
        await AuthService(session, get_settings().auth).change_password(
            user, payload.current_password, payload.new_password
        )
    except DomainError as exc:
        raise HTTPException(422, exc.message) from exc
    response.delete_cookie(get_settings().auth.session_cookie_name, path="/")
    response.delete_cookie(get_settings().auth.csrf_cookie_name, path="/")
    response.status_code = 204
    return response
