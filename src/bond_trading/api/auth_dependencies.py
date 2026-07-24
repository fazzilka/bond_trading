from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.application.services.auth import (
    AuthenticatedSession,
    AuthenticationError,
    AuthorizationError,
    AuthService,
)
from bond_trading.core.config import get_settings
from bond_trading.infrastructure.db.models import UserModel, UserRole
from bond_trading.infrastructure.db.session import get_session

Session = Annotated[AsyncSession, Depends(get_session)]


async def get_authenticated_session(
    request: Request,
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
    session_cookie: Annotated[
        str | None,
        Cookie(alias=get_settings().auth.session_cookie_name),
    ] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthenticatedSession:
    via_bearer = False
    token: str | None = None
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            token = credentials
            via_bearer = True
    if token is None:
        token = session_cookie
    if not token:
        raise HTTPException(401, "Authentication is required")
    service = AuthService(session, get_settings().auth)
    try:
        authenticated = await service.resolve(token, via_bearer=via_bearer)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not via_bearer:
            service.verify_csrf(authenticated.session, csrf_header)
    except AuthenticationError as exc:
        raise HTTPException(401, exc.message) from exc
    except AuthorizationError as exc:
        raise HTTPException(403, exc.message) from exc
    return authenticated


CurrentAuth = Annotated[AuthenticatedSession, Depends(get_authenticated_session)]


async def get_current_user(auth: CurrentAuth) -> UserModel:
    return auth.user


CurrentUser = Annotated[UserModel, Depends(get_current_user)]


async def require_admin(auth: CurrentAuth) -> UserModel:
    if auth.user.role != UserRole.ADMIN:
        raise HTTPException(403, "Administrator privileges are required")
    return auth.user


AdminUser = Annotated[UserModel, Depends(require_admin)]


async def get_current_web_auth(
    request: Request,
    session: Session,
    session_cookie: Annotated[
        str | None,
        Cookie(alias=get_settings().auth.session_cookie_name),
    ] = None,
) -> AuthenticatedSession:
    if not session_cookie:
        raise HTTPException(303, headers={"Location": f"/login?next={request.url.path}"})
    try:
        return await AuthService(session, get_settings().auth).resolve(
            session_cookie, via_bearer=False
        )
    except AuthenticationError as exc:
        raise HTTPException(303, headers={"Location": "/login"}) from exc


CurrentWebAuth = Annotated[AuthenticatedSession, Depends(get_current_web_auth)]
