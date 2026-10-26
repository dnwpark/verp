"""Tests for issue full-text search (`verp issue search`, `issues_fts`).

Commands are called directly against an in-memory issues database.
"""

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from verp.issues import commands, db
from verp.issues.model import Actor, ActorKind

ALPHA = Actor(ActorKind.AGENT, "alpha")
BETA = Actor(ActorKind.AGENT, "beta")


@pytest.fixture(autouse=True)
def conn() -> Generator[sqlite3.Connection, None, None]:
    c = db.open_connection(":memory:")
    with db.override_connection(c):
        yield c
    c.close()


def add(caller: Actor, title: str, body: str = "") -> int:
    assert commands.cmd_issue_add(caller, title, body, None, False, False) == 0
    with db.connect() as c:
        return int(c.execute("SELECT MAX(id) FROM issues").fetchone()[0])


def search(
    capsys: pytest.CaptureFixture[str],
    query: str,
    *,
    project: str | None = None,
    backlog: bool = False,
    statuses: list[str] | None = None,
    open_only: bool = False,
    any_term: bool = False,
    raw: bool = False,
    limit: int = 20,
) -> list[Any]:
    capsys.readouterr()
    rc = commands.cmd_issue_search(
        query,
        project,
        backlog,
        statuses,
        open_only,
        any_term,
        raw,
        limit,
        True,
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    result: list[Any] = json.loads(out)
    return result


def ids(hits: list[Any]) -> list[int]:
    return [h["id"] for h in hits]


# --- what is indexed -------------------------------------------------------


def test_matches_title_and_body(capsys: pytest.CaptureFixture[str]) -> None:
    a = add(ALPHA, "Retry fetch", "blocking refs")
    assert ids(search(capsys, "retry")) == [a]
    assert ids(search(capsys, "blocking")) == [a]


def test_edits_are_reindexed(capsys: pytest.CaptureFixture[str]) -> None:
    a = add(ALPHA, "old title", "old body")
    commands.cmd_issue_edit(ALPHA, a, "new title", "fresh body", None, False)
    assert ids(search(capsys, "old")) == []
    assert ids(search(capsys, "fresh")) == [a]


def test_matches_log_and_outcome(capsys: pytest.CaptureFixture[str]) -> None:
    a = add(ALPHA, "t")
    commands.cmd_issue_log(ALPHA, a, "interrupted checkout", False)
    commands.cmd_issue_close(ALPHA, a, "deleted refs", None, False, False)
    assert ids(search(capsys, "interrupted")) == [a]
    assert ids(search(capsys, "deleted refs")) == [a]


def test_outcome_stays_searchable_after_reopen(
    capsys: pytest.CaptureFixture[str],
) -> None:
    a = add(ALPHA, "t")
    commands.cmd_issue_close(ALPHA, a, "first attempt", None, False, False)
    commands.cmd_issue_reopen(Actor(ActorKind.CLI, None), a, "regressed")
    assert ids(search(capsys, "attempt")) == [a]
    assert ids(search(capsys, "regressed")) == [a]


def test_bookkeeping_events_not_indexed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # "created in alpha" and dependency events are not prose.
    a, b = add(ALPHA, "t"), add(ALPHA, "u")
    commands.cmd_issue_dep_add(ALPHA, b, [a], False)
    assert search(capsys, "alpha") == []


# --- query syntax ----------------------------------------------------------


def test_stemming(capsys: pytest.CaptureFixture[str]) -> None:
    a = add(ALPHA, "ref conflicts")
    assert ids(search(capsys, "conflicting")) == [a]


def test_all_terms_by_default_or_any(
    capsys: pytest.CaptureFixture[str],
) -> None:
    a, b = add(ALPHA, "fetch refs"), add(ALPHA, "prune remote")
    assert ids(search(capsys, "fetch prune")) == []
    assert sorted(ids(search(capsys, "fetch prune", any_term=True))) == [a, b]


def test_punctuation_is_not_syntax(capsys: pytest.CaptureFixture[str]) -> None:
    a = add(ALPHA, "t", "use update-ref -d (carefully)")
    assert ids(search(capsys, "update-ref -d (")) == [a]


def test_raw_query(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = add(ALPHA, "fetch refs"), add(ALPHA, "prune remote")
    assert sorted(ids(search(capsys, "fetch OR prune", raw=True))) == [a, b]


@pytest.mark.parametrize(
    "query, raw, msg",
    [
        ("  ", False, "empty"),
        ("!!", False, "empty"),
        ("AND (", True, "invalid"),
    ],
)
def test_bad_queries(
    capsys: pytest.CaptureFixture[str], query: str, raw: bool, msg: str
) -> None:
    rc = commands.cmd_issue_search(
        query, None, False, None, False, False, raw, 20, True
    )
    assert rc == 1 and msg in capsys.readouterr().err


# --- ranking and filters ---------------------------------------------------


def test_title_match_ranks_first(capsys: pytest.CaptureFixture[str]) -> None:
    in_body = add(ALPHA, "something", "mentions monitor once")
    in_title = add(ALPHA, "monitor", "")
    assert ids(search(capsys, "monitor")) == [in_title, in_body]


def test_filters(capsys: pytest.CaptureFixture[str]) -> None:
    a = add(ALPHA, "shared word")
    b = add(BETA, "shared word")
    c = add(Actor(ActorKind.AGENT, None), "shared word")
    commands.cmd_issue_close(ALPHA, a, "done", None, False, False)

    assert sorted(ids(search(capsys, "shared"))) == [a, b, c]
    assert ids(search(capsys, "shared", project="beta")) == [b]
    assert ids(search(capsys, "shared", backlog=True)) == [c]
    assert sorted(ids(search(capsys, "shared", open_only=True))) == [b, c]
    assert ids(search(capsys, "shared", statuses=["done"])) == [a]
    assert len(search(capsys, "shared", limit=2)) == 2


def test_json_snippet_marks_matches(capsys: pytest.CaptureFixture[str]) -> None:
    add(ALPHA, "Pull resets repo")
    [hit] = search(capsys, "pull")
    assert hit["snippet"] == "**Pull** resets repo"
    assert "blocked_by" in hit


def test_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    add(ALPHA, "Pull resets repo")
    commands.cmd_issue_search(
        "pull", None, False, None, False, False, False, 20, False
    )
    out = capsys.readouterr().out
    assert "Pull resets repo" in out and "(alpha)" in out
    commands.cmd_issue_search(
        "zzz", None, False, None, False, False, False, 20, False
    )
    assert "no matching issues" in capsys.readouterr().out


# --- migration -------------------------------------------------------------


def test_v2_migration_backfills_existing_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "issues.db"
    # Create a v1 database with data, as it existed before search.
    monkeypatch.setattr(db, "SCHEMA_VERSION", 1)
    v1 = db.open_connection(path)
    assert v1.execute("PRAGMA user_version").fetchone()[0] == 1
    with v1:
        i = db.create_issue(v1, "legacy title", "legacy body", "alpha", ALPHA)
        db.add_event(v1, i, ALPHA, db.EventKind.LOG, "legacy note")
    v1.close()

    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    v2 = db.open_connection(path)
    assert v2.execute("PRAGMA user_version").fetchone()[0] == 2
    for term in ("legacy", "title", "body", "note"):
        hits = db.search_issues(v2, f'"{term}"')
        assert [h[0].id for h in hits] == [i], term
    v2.close()
