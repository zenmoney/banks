"""Single-bank workspace fixtures; public task CLI coverage lives in task tests.

These tests isolate evidence/transport mechanics from live GitHub discovery. They
call the same internal workspace constructor used by task-start, never a legacy CLI.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import session


def make_session(fixture, bank_id=10, *, session_path=None, base="HEAD", remote=None,
                 now="2026-09-25T00:00:00Z", ok=True):
    try:
        result = session.create_session(fixture.repo, session_path or fixture.session,
                                        bank_id, base=base, remote=remote, now=now)
    except (session.SessionError, ValueError, OSError) as exc:
        if ok:
            raise
        return {"error": str(exc)}
    if not ok:
        raise AssertionError("workspace creation unexpectedly succeeded")
    return result
