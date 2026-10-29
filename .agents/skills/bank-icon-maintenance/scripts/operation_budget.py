"""Caller-owned wall-clock budget shared by nested Git/GitHub reads.

No policy/authority or evidence semantics live here. Outside a bounded operation,
existing subprocess timeouts are unchanged.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_DEADLINE = ContextVar("bank_icon_operation_deadline", default=None)


class Exhausted(Exception):
    """An unfinished read, not a bank error or a completed provider observation."""


@contextmanager
def bounded(seconds):
    existing = _DEADLINE.get()
    deadline = time.monotonic() + seconds
    token = _DEADLINE.set(min(existing, deadline) if existing is not None else deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def timeout(requested=None):
    deadline = _DEADLINE.get()
    if deadline is None:
        return requested
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Exhausted("outcome read budget exhausted; unfinished MR remains resumable")
    return min(remaining, requested) if requested is not None else remaining


def check():
    timeout()
