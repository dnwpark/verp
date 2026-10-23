"""Tests for orphaned issue handling (`verp.issues.orphans`).

Functions are called directly against an in-memory issues database; the set
of existing projects is stubbed via `orphans.project_names`.
"""

import sqlite3
from collections.abc import Generator

import pytest

from verp.issues import db, orphans
from verp.issues.model import Actor, ActorKind, EventKind, Issue, IssueStatus

CLI = Actor(ActorKind.CLI, None)
ALPHA = Actor(ActorKind.AGENT, "alpha")
BETA = Actor(ActorKind.AGENT, "beta")


@pytest.fixture(autouse=True)
def conn() -> Generator[sqlite3.Connection, None, None]:
    c = db.open_connection(":memory:")
    with db.override_connection(c):
        yield c
    c.close()


def known_projects(
    monkeypatch: pytest.MonkeyPatch, names: set[str] | None
) -> None:
    monkeypatch.setattr(orphans, "project_names", lambda: names)


def add(
    conn: sqlite3.Connection,
    project: str | None,
    status: IssueStatus = IssueStatus.OPEN,
) -> int:
    with conn:
        actor = Actor(ActorKind.AGENT, project)
        issue_id = db.create_issue(conn, "t", "", project, actor)
        if status != IssueStatus.OPEN:
            outcome = "ok" if status.closed else None
            db.set_status(conn, issue_id, status, outcome=outcome)
    return issue_id


def get(conn: sqlite3.Connection, issue_id: int) -> Issue:
    issue = db.get_issue(conn, issue_id)
    assert issue is not None
    return issue


# --- release on delete -----------------------------------------------------


def test_release_project_issues(conn: sqlite3.Connection) -> None:
    open_ = add(conn, "alpha")
    wip = add(conn, "alpha", IssueStatus.IN_PROGRESS)
    done = add(conn, "alpha", IssueStatus.DONE)
    other = add(conn, "beta")

    assert [i.id for i in orphans.open_issues("alpha")] == [open_, wip]
    assert orphans.release_project_issues("alpha", CLI) == [open_, wip]

    for issue_id in (open_, wip):
        issue = get(conn, issue_id)
        assert issue.project is None and issue.status == IssueStatus.OPEN
        last = db.get_events(conn, issue_id)[-1]
        assert last.kind == EventKind.RELEASED
        assert last.message == "project 'alpha' deleted"
        assert last.actor == CLI
    # Closed issues keep their project as history; others are untouched.
    assert get(conn, done).project == "alpha"
    assert get(conn, other).project == "beta"


def test_release_project_issues_none(conn: sqlite3.Connection) -> None:
    add(conn, "beta")
    assert orphans.release_project_issues("alpha", CLI) == []


# --- reconcile -------------------------------------------------------------


def test_reconcile_releases_missing_projects(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gone = add(conn, "alpha")
    gone_wip = add(conn, "alpha", IssueStatus.IN_PROGRESS)
    gone_done = add(conn, "alpha", IssueStatus.DROPPED)
    kept = add(conn, "beta")
    backlog = add(conn, None)
    known_projects(monkeypatch, {"beta"})

    assert orphans.reconcile_orphans(conn, CLI) == [gone, gone_wip]
    for issue_id in (gone, gone_wip):
        issue = get(conn, issue_id)
        assert issue.project is None and issue.status == IssueStatus.OPEN
        last = db.get_events(conn, issue_id)[-1]
        assert last.message == "project 'alpha' no longer exists"
    assert get(conn, gone_done).project == "alpha"
    assert get(conn, kept).project == "beta"
    assert get(conn, backlog).project is None
    assert f"released #{gone}" in capsys.readouterr().err


def test_reconcile_is_idempotent(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(conn, "alpha")
    known_projects(monkeypatch, {"beta"})
    assert len(orphans.reconcile_orphans(conn, CLI)) == 1
    assert orphans.reconcile_orphans(conn, CLI) == []


def test_reconcile_skips_when_verp_db_unavailable(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    i = add(conn, "alpha")
    known_projects(monkeypatch, None)
    assert orphans.reconcile_orphans(conn, CLI) == []
    assert get(conn, i).project == "alpha"


def test_reconcile_skips_when_no_projects_exist(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    i = add(conn, "alpha")
    known_projects(monkeypatch, set())
    assert orphans.reconcile_orphans(conn, CLI) == []
    assert get(conn, i).project == "alpha"
    assert "not releasing" in capsys.readouterr().err


def test_reconcile_without_owned_issues_does_not_read_projects(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(conn, None)
    add(conn, "alpha", IssueStatus.DONE)

    def fail() -> set[str]:
        raise AssertionError("project_names should not be called")

    monkeypatch.setattr(orphans, "project_names", fail)
    assert orphans.reconcile_orphans(conn, CLI) == []


def test_released_orphan_is_claimable(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from verp.issues import commands

    i = add(conn, "alpha")
    known_projects(monkeypatch, {"beta"})
    orphans.reconcile_orphans(conn, CLI)
    assert commands.cmd_issue_claim(BETA, i, False) == 0
    assert get(conn, i).project == "beta"
