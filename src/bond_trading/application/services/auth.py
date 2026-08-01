import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from pwdlib import PasswordHash
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from bond_trading.core.config import AuthSettings
from bond_trading.domain.errors import DomainError
from bond_trading.infrastructure.db.models import (
    AuthSessionModel,
    UserModel,
    UserRole,
)


class AuthenticationError(DomainError):
    code = "authentication_failed"


class AuthorizationError(DomainError):
    code = "authorization_failed"


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    user: UserModel
    session: AuthSessionModel
    token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user: UserModel
    session: AuthSessionModel
    via_bearer: bool


class AuthService:
    def __init__(self, session: AsyncSession, settings: AuthSettings) -> None:
        self._session = session
        self._settings = settings
        self._passwords = PasswordHash.recommended()

    async def ensure_bootstrap_users(self) -> None:
        users = (
            (
                self._settings.bootstrap_admin_username,
                self._settings.bootstrap_admin_email,
                self._settings.bootstrap_admin_password.get_secret_value(),
                UserRole.ADMIN,
                True,
            ),
            (
                self._settings.bootstrap_user1_username,
                self._settings.bootstrap_user1_email,
                self._settings.bootstrap_user1_password.get_secret_value(),
                UserRole.USER,
                False,
            ),
            (
                self._settings.bootstrap_user2_username,
                self._settings.bootstrap_user2_email,
                self._settings.bootstrap_user2_password.get_secret_value(),
                UserRole.USER,
                False,
            ),
        )
        changed = False
        for username, email, password, role, sync_password in users:
            self._validate_password(password)
            existing = await self._session.scalar(
                select(UserModel).where(UserModel.username == self._normalize_username(username))
            )
            if existing is not None:
                if sync_password and not self._password_matches(password, existing.password_hash):
                    existing.password_hash = self._passwords.hash(password)
                    existing.must_change_password = False
                    await self._revoke_user_sessions(existing.id)
                    changed = True
                continue
            self._session.add(
                UserModel(
                    username=self._normalize_username(username),
                    email=email.strip().lower(),
                    password_hash=self._passwords.hash(password),
                    role=role,
                    is_active=True,
                    must_change_password=True,
                )
            )
            changed = True
        if changed:
            await self._session.commit()

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        role: UserRole = UserRole.USER,
        must_change_password: bool = True,
    ) -> UserModel:
        normalized_username = self._normalize_username(username)
        normalized_email = email.strip().lower()
        self._validate_password(password)
        existing = await self._session.scalar(
            select(UserModel).where(
                or_(
                    UserModel.username == normalized_username,
                    UserModel.email == normalized_email,
                )
            )
        )
        if existing is not None:
            raise DomainError("A user with this username or email already exists")
        user = UserModel(
            username=normalized_username,
            email=normalized_email,
            password_hash=self._passwords.hash(password),
            role=role,
            is_active=True,
            must_change_password=must_change_password,
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def authenticate(
        self, login: str, password: str, user_agent: str | None
    ) -> SessionCredentials:
        normalized = login.strip().lower()
        user = await self._session.scalar(
            select(UserModel).where(
                or_(UserModel.username == normalized, UserModel.email == normalized)
            )
        )
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid username or password")
        try:
            password_valid = self._passwords.verify(password, user.password_hash)
        except Exception:
            password_valid = False
        if not password_valid:
            raise AuthenticationError("Invalid username or password")
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        auth_session = AuthSessionModel(
            user_id=user.id,
            token_hash=_token_hash(token),
            csrf_token_hash=_token_hash(csrf_token),
            expires_at=now + timedelta(seconds=self._settings.session_ttl_seconds),
            last_seen_at=now,
            user_agent=(user_agent or "")[:512] or None,
        )
        user.last_login_at = now
        self._session.add(auth_session)
        await self._session.commit()
        await self._session.refresh(auth_session)
        return SessionCredentials(user, auth_session, token, csrf_token)

    async def resolve(self, token: str, *, via_bearer: bool) -> AuthenticatedSession:
        auth_session = await self._session.scalar(
            select(AuthSessionModel)
            .options(joinedload(AuthSessionModel.user))
            .where(AuthSessionModel.token_hash == _token_hash(token))
        )
        now = datetime.now(UTC)
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or _aware(auth_session.expires_at) <= now
            or not auth_session.user.is_active
        ):
            raise AuthenticationError("Authentication is required")
        return AuthenticatedSession(auth_session.user, auth_session, via_bearer)

    async def revoke(self, auth_session: AuthSessionModel) -> None:
        auth_session.revoked_at = datetime.now(UTC)
        await self._session.commit()

    def verify_csrf(self, auth_session: AuthSessionModel, csrf_token: str | None) -> None:
        if not csrf_token or not secrets.compare_digest(
            auth_session.csrf_token_hash, _token_hash(csrf_token)
        ):
            raise AuthorizationError("The CSRF token is missing or invalid")

    async def change_password(
        self, user: UserModel, current_password: str, new_password: str
    ) -> None:
        if not self._passwords.verify(current_password, user.password_hash):
            raise AuthenticationError("The current password is invalid")
        self._validate_password(new_password)
        user.password_hash = self._passwords.hash(new_password)
        user.must_change_password = False
        await self._session.execute(
            update(AuthSessionModel)
            .where(
                AuthSessionModel.user_id == user.id,
                AuthSessionModel.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def list_users(self) -> list[UserModel]:
        return list(await self._session.scalars(select(UserModel).order_by(UserModel.username)))

    async def get_user(self, user_id: UUID) -> UserModel | None:
        return cast(UserModel | None, await self._session.get(UserModel, user_id))

    async def set_active(self, user: UserModel, active: bool) -> None:
        if user.role == UserRole.ADMIN and not active:
            active_admins = await self._session.scalars(
                select(UserModel).where(
                    UserModel.role == UserRole.ADMIN,
                    UserModel.is_active.is_(True),
                    UserModel.id != user.id,
                )
            )
            if not list(active_admins):
                raise DomainError("The last active administrator cannot be disabled")
        user.is_active = active
        if not active:
            await self._session.execute(
                update(AuthSessionModel)
                .where(
                    AuthSessionModel.user_id == user.id,
                    AuthSessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
        await self._session.commit()

    def _password_matches(self, password: str, password_hash: str) -> bool:
        try:
            return self._passwords.verify(password, password_hash)
        except Exception:
            return False

    async def _revoke_user_sessions(self, user_id: UUID) -> None:
        await self._session.execute(
            update(AuthSessionModel)
            .where(
                AuthSessionModel.user_id == user_id,
                AuthSessionModel.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )

    @staticmethod
    def _normalize_username(username: str) -> str:
        value = username.strip().lower()
        if (
            not value
            or len(value) > 64
            or not all(character.isalnum() or character in {"-", "_", "."} for character in value)
        ):
            raise DomainError(
                "Username must contain only letters, numbers, dot, dash or underscore"
            )
        return value

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12:
            raise DomainError("Password must contain at least 12 characters")


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
