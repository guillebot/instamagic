from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event, exc
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from instamagic.config import Settings, get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_fork_hooks_installed = False


def dispose_engine(*, close: bool = False) -> None:
    """Drop pooled DB connections (fork-safe invalidation or full shutdown)."""
    global _engine
    if _engine is not None:
        _engine.dispose(close=close)


def _after_fork_in_child() -> None:
    """Invalidate inherited SQLAlchemy sockets in the RQ work-horse only."""
    dispose_engine(close=False)


def _ensure_fork_hooks() -> None:
    global _fork_hooks_installed
    if _fork_hooks_installed:
        return
    register = getattr(os, "register_at_fork", None)
    if register is None:
        return
    register(after_in_child=_after_fork_in_child)
    _fork_hooks_installed = True


def _install_pool_pid_guard(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _store_pid(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        connection_record.info["pid"] = os.getpid()

    @event.listens_for(engine, "checkout")
    def _check_pid(dbapi_connection, connection_record, connection_proxy) -> None:  # type: ignore[no-untyped-def]
        recorded = connection_record.info.get("pid")
        if recorded is not None and recorded != os.getpid():
            connection_record.dbapi_connection = connection_proxy.dbapi_connection = None
            raise exc.DisconnectionError(
                f"Connection belongs to pid {recorded}, checkout attempted in pid {os.getpid()}"
            )


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine, _session_factory
    if _engine is None:
        settings = settings or get_settings()
        url = settings.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, pool_pre_ping=not url.startswith("sqlite"), connect_args=connect_args)
        _install_pool_pid_guard(_engine)
        _ensure_fork_hooks()
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def reset_engine() -> None:
    global _engine, _session_factory, _fork_hooks_installed
    dispose_engine(close=True)
    _engine = None
    _session_factory = None
    _fork_hooks_installed = False
