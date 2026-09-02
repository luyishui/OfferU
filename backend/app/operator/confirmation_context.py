from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class ConfirmationPrepareBoundaryViolation(RuntimeError):
    """Raised when confirmation preparation attempts work outside its read-only owner."""


@dataclass
class ConfirmationPrepareBoundary:
    owner_session: Session
    owner_connection: Any
    violation_attempted: bool = False

    def reject(self, message: str) -> None:
        self.violation_attempted = True
        raise ConfirmationPrepareBoundaryViolation(message)

    def assert_intact(self) -> None:
        if self.violation_attempted:
            raise ConfirmationPrepareBoundaryViolation(
                "Proposal preparation attempted to use an independent database write transaction."
            )


_ACTIVE_PREPARE_BOUNDARY: ContextVar[ConfirmationPrepareBoundary | None] = ContextVar(
    "offeru_confirmation_prepare_boundary",
    default=None,
)


@contextmanager
def confirmation_prepare_boundary(
    owner_session: Session,
    owner_connection: Any,
) -> Iterator[ConfirmationPrepareBoundary]:
    """Install a task-local, process-wide SQLAlchemy write boundary for prepare."""

    boundary = ConfirmationPrepareBoundary(
        owner_session=owner_session,
        owner_connection=owner_connection,
    )
    token = _ACTIVE_PREPARE_BOUNDARY.set(boundary)
    try:
        yield boundary
    finally:
        _ACTIVE_PREPARE_BOUNDARY.reset(token)


def _active_independent_session(session: Session) -> ConfirmationPrepareBoundary | None:
    boundary = _ACTIVE_PREPARE_BOUNDARY.get()
    if boundary is None or session is boundary.owner_session:
        return None
    return boundary


def _before_commit(session: Session) -> None:
    boundary = _active_independent_session(session)
    if boundary is not None:
        boundary.reject("Independent sessions must not commit during proposal preparation.")


def _before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    boundary = _active_independent_session(session)
    if boundary is not None and (session.new or session.dirty or session.deleted):
        boundary.reject("Independent sessions must not flush writes during proposal preparation.")


def _do_orm_execute(execute_state: Any) -> None:
    boundary = _active_independent_session(execute_state.session)
    if boundary is not None and (
        execute_state.is_insert or execute_state.is_update or execute_state.is_delete
    ):
        boundary.reject("Independent sessions must not execute DML during proposal preparation.")


def _before_cursor_execute(
    connection: Any,
    cursor: Any,
    statement: Any,
    parameters: Any,
    context: Any,
    executemany: Any,
) -> None:
    boundary = _ACTIVE_PREPARE_BOUNDARY.get()
    if boundary is None or connection is boundary.owner_connection:
        return
    if not str(statement or "").lstrip().upper().startswith("SELECT"):
        boundary.reject("Independent connections may execute SELECT statements only during proposal preparation.")


event.listen(Session, "before_commit", _before_commit)
event.listen(Session, "before_flush", _before_flush)
event.listen(Session, "do_orm_execute", _do_orm_execute)
event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
