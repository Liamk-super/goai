"""SQLAlchemy engine and explicit tenant-scoped transaction boundaries."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.session import SessionTransaction

from launchscope_domain.value_objects import TenantScope

from .rls import TenantContext, set_local_tenant_context, validate_runtime_role


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Runtime database settings with no credential logging or persistence."""

    url: str
    application_role: str | None = None
    pool_pre_ping: bool = True

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        url = os.getenv("DATABASE_URL") or os.getenv("LAUNCHSCOPE_DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL must be set before opening a LaunchScope database connection")
        return cls(
            url=url,
            application_role=validate_runtime_role(os.getenv("LAUNCHSCOPE_DB_ROLE")),
        )


def normalize_database_url(url: str) -> str:
    """Use the psycopg 3 dialect even when operators use the generic URL."""

    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def create_database_engine(
    url: str | None = None,
    *,
    application_role: str | None = None,
    pool_pre_ping: bool = True,
    **kwargs: object,
) -> Engine:
    """Create an engine and optionally drop it into a non-owner runtime role.

    Migrations and maintenance use ``application_role=None``.  Request/test
    engines should use the non-superuser role created by migration 0001 (or a
    deployment-managed equivalent) so PostgreSQL cannot bypass RLS.
    """

    resolved_url = normalize_database_url(url or DatabaseSettings.from_env().url)
    role = validate_runtime_role(application_role or os.getenv("LAUNCHSCOPE_DB_ROLE"))
    engine = create_engine(resolved_url, pool_pre_ping=pool_pre_ping, **kwargs)
    if role and engine.dialect.name == "postgresql":

        @event.listens_for(engine, "connect")
        def _set_application_role(dbapi_connection: object, _connection_record: object) -> None:
            # Role names cannot be bound as query parameters.  validate_runtime_role
            # above is the allow-list that makes this interpolation safe.
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute(f"SET ROLE {role}")
            cursor.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a regular Session factory; transactions are explicit below."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class TenantUnitOfWork:
    """One database transaction carrying one immutable tenant scope."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        scope: TenantScope,
        *,
        actor_id: str | None = None,
    ) -> None:
        self._factory = factory
        self._scope = scope
        self._actor_id = actor_id
        self.session: Session | None = None
        self.context: TenantContext | None = None
        self._transaction: SessionTransaction | None = None

    def __enter__(self) -> Session:
        self.session = self._factory()
        self._transaction = self.session.begin()
        connection = self.session.connection()
        self.context = set_local_tenant_context(connection, self._scope, actor_id=self._actor_id)
        return self.session

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        assert self.session is not None
        assert self._transaction is not None
        try:
            if exc_type is None:
                self._transaction.commit()
            else:
                self._transaction.rollback()
        finally:
            self.session.close()
            self.session = None
            self._transaction = None


@contextmanager
def tenant_transaction(
    factory: sessionmaker[Session],
    scope: TenantScope,
    *,
    actor_id: str | None = None,
) -> Iterator[Session]:
    """Convenient context manager for request/application services."""

    with TenantUnitOfWork(factory, scope, actor_id=actor_id) as session:
        yield session


def set_context_on_session(session: Session, scope: TenantScope, *, actor_id: str | None = None) -> TenantContext:
    """Set context on an already-open transaction for adapter composition."""

    return set_local_tenant_context(session.connection(), scope, actor_id=actor_id)


@contextmanager
def database_connection(engine: Engine) -> Iterator[Connection]:
    """Type-friendly helper for migrations/tests that need a raw connection."""

    with engine.connect() as connection:
        yield connection


__all__ = [
    "DatabaseSettings",
    "TenantUnitOfWork",
    "create_database_engine",
    "database_connection",
    "session_factory",
    "set_context_on_session",
    "tenant_transaction",
    "normalize_database_url",
]
