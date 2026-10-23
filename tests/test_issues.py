"""Tests for the issue tracker (`verp.issues`).

Commands are called directly with an explicit `Actor`; caller resolution from
the environment is tested separately in `test_issues_identity.py`.

Each test gets its own in-memory issues database via `db.override_connection`.
Project lookups are stubbed so `verp.db` is never touched.
"""

import io
import json
import sqlite3
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from verp.db import ProjectInfo
from verp.issues import commands, db, identity
from verp.issues.model import Actor, ActorKind, EventKind, Issue, IssueStatus

PROJECTS = {
    "alpha": ProjectInfo("alpha", "/work/alpha", "me/alpha", [], 1),
    "beta": ProjectInfo("beta", "/work/beta", "me/beta", [], 1),
}

CLI = Actor(ActorKind.CLI, None)
CLI_BETA = Actor(ActorKind.CLI, "beta")
ALPHA = Actor(ActorKind.AGENT, "alpha")
BETA = Actor(ActorKind.AGENT, "beta")
NO_PROJECT = Actor(ActorKind.AGENT, None)
WORKER_BETA = Actor(ActorKind.WORKER, "beta")


@pytest.fixture(autouse=True)
def conn(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[sqlite3.Connection, None, None]:
    monkeypatch.setattr(identity, "get_project", PROJECTS.get)
    monkeypatch.setattr(commands, "get_project", PROJECTS.get)
    c = db.open_connection(":memory:")
    with db.override_connection(c):
        yield c
    c.close()


def run(
    capsys: pytest.CaptureFixture[str],
    fn: Callable[..., int],
    *args: Any,
    **kwargs: Any,
) -> tuple[int, str, str]:
    rc = fn(*args, **kwargs)
    out, err = capsys.readouterr()
    return rc, out, err


def add(
    caller: Actor,
    title: str = "t",
    body: str | None = None,
    backlog: bool = False,
) -> int:
    assert (
        commands.cmd_issue_add(caller, title, body, None, backlog, False) == 0
    )
    with db.connect() as c:
        return int(c.execute("SELECT MAX(id) FROM issues").fetchone()[0])


def get(issue_id: int) -> Issue:
    with db.connect() as c:
        issue = db.get_issue(c, issue_id)
    assert issue is not None
    return issue


def events(issue_id: int) -> list[Any]:
    with db.connect() as c:
        return db.get_events(c, issue_id)


def close(caller: Actor, issue_id: int, outcome: str = "done") -> None:
    assert (
        commands.cmd_issue_close(caller, issue_id, outcome, None, False, False)
        == 0
    )


def list_out(
    capsys: pytest.CaptureFixture[str],
    caller: Actor,
    *,
    all_projects: bool = False,
    closed: bool = False,
    as_json: bool = False,
) -> str:
    capsys.readouterr()
    rc, out, _ = run(
        capsys,
        commands.cmd_issue_list,
        caller,
        None,
        False,
        all_projects,
        None,
        closed,
        as_json,
    )
    assert rc == 0
    return out


# --- schema ----------------------------------------------------------------


def test_migrations_set_user_version(conn: sqlite3.Connection) -> None:
    assert (
        conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    )
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"issues", "issue_deps", "issue_events"} <= tables


def test_reopening_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "issues.db"
    db.open_connection(path).close()
    c = db.open_connection(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    c.close()


def test_self_dependency_rejected(conn: sqlite3.Connection) -> None:
    i = add(CLI, backlog=True)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO issue_deps VALUES (?, ?)", (i, i))


def test_actor_str() -> None:
    assert str(ALPHA) == "agent alpha"
    assert str(CLI) == "cli"


# --- add / list / show -----------------------------------------------------


def test_add_defaults_to_caller_project() -> None:
    i = add(ALPHA)
    issue = get(i)
    assert issue.project == "alpha"
    assert issue.status == IssueStatus.OPEN
    [created] = events(i)
    assert created.kind == EventKind.CREATED
    assert created.actor == ALPHA


def test_add_without_project_goes_to_backlog() -> None:
    assert get(add(NO_PROJECT)).project is None


def test_add_backlog_flag() -> None:
    assert get(add(ALPHA, backlog=True)).project is None


def test_add_body_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    assert commands.cmd_issue_add(ALPHA, "t", None, "-", False, False) == 0
    assert get(1).body == "from stdin\n"


def test_timestamps_are_epoch_ms() -> None:
    issue = get(add(ALPHA))
    assert abs(issue.created_at - time.time() * 1000) < 5000
    assert issue.closed_at is None


def test_list_default_scope_and_closed_filter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    add(ALPHA, "a-open")
    close(ALPHA, add(ALPHA, "a-closed"))
    add(BETA, "b-open")

    out = list_out(capsys, ALPHA)
    assert "a-open" in out and "a-closed" not in out and "b-open" not in out
    assert "a-closed" in list_out(capsys, ALPHA, closed=True)
    out = list_out(capsys, ALPHA, all_projects=True)
    assert "a-open" in out and "b-open" in out


def test_list_json(capsys: pytest.CaptureFixture[str]) -> None:
    add(ALPHA, "x")
    [item] = json.loads(list_out(capsys, ALPHA, as_json=True))
    assert item["title"] == "x" and item["project"] == "alpha"


def test_list_shows_open_dependencies(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    dep, blocked = add(ALPHA, "dep"), add(ALPHA, "blocked")
    conn.execute("INSERT INTO issue_deps VALUES (?, ?)", (blocked, dep))
    assert f"blocked by #{dep}" in list_out(capsys, ALPHA)
    close(ALPHA, dep)
    assert "blocked by" not in list_out(capsys, ALPHA)


def test_show_json_includes_events(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA, "x", "body")
    commands.cmd_issue_log(ALPHA, i, "note", False)
    capsys.readouterr()
    _, out, _ = run(capsys, commands.cmd_issue_show, i, True)
    data = json.loads(out)
    assert data["body"] == "body"
    assert [e["kind"] for e in data["events"]] == ["created", "log"]
    assert data["events"][1]["actor"] == {"kind": "agent", "project": "alpha"}


def test_show_missing(capsys: pytest.CaptureFixture[str]) -> None:
    rc, _, err = run(capsys, commands.cmd_issue_show, 99, False)
    assert rc == 1 and "no issue 99" in err


# --- ownership -------------------------------------------------------------


def test_other_project_cannot_modify(
    capsys: pytest.CaptureFixture[str],
) -> None:
    i = add(ALPHA)
    rc, _, err = run(capsys, commands.cmd_issue_log, BETA, i, "hi", False)
    assert rc == 1 and "belongs to project 'alpha'" in err


@pytest.mark.parametrize("caller", [BETA, WORKER_BETA], ids=["agent", "worker"])
def test_agents_cannot_force(
    capsys: pytest.CaptureFixture[str], caller: Actor
) -> None:
    i = add(ALPHA)
    assert run(capsys, commands.cmd_issue_log, caller, i, "hi", True)[0] == 1


def test_cli_outside_project_can_modify(
    capsys: pytest.CaptureFixture[str],
) -> None:
    i = add(ALPHA)
    assert run(capsys, commands.cmd_issue_log, CLI, i, "hi", False)[0] == 0
    assert events(i)[-1].actor == CLI


def test_cli_in_other_project_needs_force(
    capsys: pytest.CaptureFixture[str],
) -> None:
    i = add(ALPHA)
    rc, _, err = run(capsys, commands.cmd_issue_log, CLI_BETA, i, "hi", False)
    assert rc == 1 and "--force" in err
    assert run(capsys, commands.cmd_issue_log, CLI_BETA, i, "hi", True)[0] == 0


def test_closed_issues_are_frozen(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA)
    close(ALPHA, i)
    rc, _, err = run(capsys, commands.cmd_issue_log, ALPHA, i, "late", False)
    assert rc == 1 and "is done" in err
    assert run(capsys, commands.cmd_issue_log, ALPHA, i, "late", True)[0] == 1
    assert run(capsys, commands.cmd_issue_log, CLI, i, "late", False)[0] == 1
    assert run(capsys, commands.cmd_issue_log, CLI, i, "late", True)[0] == 0


# --- edit ------------------------------------------------------------------


def test_edit(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA, "old", "old body")
    rc, _, err = run(
        capsys, commands.cmd_issue_edit, ALPHA, i, None, None, None, False
    )
    assert rc == 1 and "nothing to edit" in err
    assert (
        commands.cmd_issue_edit(ALPHA, i, "new", "new body", None, False) == 0
    )
    issue = get(i)
    assert (issue.title, issue.body) == ("new", "new body")
    assert events(i)[-1].message == "title, body"


# --- status ----------------------------------------------------------------


def test_start(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA)
    assert commands.cmd_issue_start(ALPHA, i, False) == 0
    assert get(i).status == IssueStatus.IN_PROGRESS
    rc, _, err = run(capsys, commands.cmd_issue_start, ALPHA, i, False)
    assert rc == 1 and "in_progress" in err


def test_start_backlog_requires_claim(
    capsys: pytest.CaptureFixture[str],
) -> None:
    i = add(NO_PROJECT)
    rc, _, err = run(capsys, commands.cmd_issue_start, NO_PROJECT, i, False)
    assert rc == 1 and "claim it first" in err


def test_close_requires_outcome(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA)
    rc, _, err = run(
        capsys, commands.cmd_issue_close, ALPHA, i, "  ", None, False, False
    )
    assert rc == 1 and "outcome is required" in err
    rc, _, err = run(
        capsys, commands.cmd_issue_close, ALPHA, i, None, None, True, False
    )
    assert rc == 1 and "reason is required" in err


def test_close_outcome_from_file(tmp_path: Path) -> None:
    f = tmp_path / "outcome.md"
    f.write_text("  from file\n")
    i = add(ALPHA)
    assert commands.cmd_issue_close(ALPHA, i, None, str(f), False, False) == 0
    assert get(i).outcome == "from file"


@pytest.mark.parametrize(
    "dropped, status", [(False, IssueStatus.DONE), (True, IssueStatus.DROPPED)]
)
def test_close(dropped: bool, status: IssueStatus) -> None:
    i = add(ALPHA)
    assert (
        commands.cmd_issue_close(ALPHA, i, " why ", None, dropped, False) == 0
    )
    issue = get(i)
    assert issue.status == status
    assert issue.outcome == "why"
    assert issue.closed_at is not None


def test_reopen_clears_closed_at() -> None:
    i = add(ALPHA)
    close(ALPHA, i)
    with db.connect() as c:
        db.set_status(c, i, IssueStatus.OPEN)
    assert get(i).closed_at is None


# --- claim / release -------------------------------------------------------


def test_claim_from_backlog() -> None:
    i = add(CLI, backlog=True)
    assert commands.cmd_issue_claim(ALPHA, i, False) == 0
    assert get(i).project == "alpha"
    assert events(i)[-1].kind == EventKind.CLAIMED


def test_claim_rules(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA)
    rc, _, err = run(capsys, commands.cmd_issue_claim, ALPHA, i, False)
    assert rc == 1 and "already belongs" in err

    rc, _, err = run(capsys, commands.cmd_issue_claim, BETA, i, True)
    assert rc == 1 and "belongs to project 'alpha'" in err

    assert run(capsys, commands.cmd_issue_claim, CLI_BETA, i, True)[0] == 0
    assert get(i).project == "beta"

    rc, _, err = run(capsys, commands.cmd_issue_claim, NO_PROJECT, i, False)
    assert rc == 1 and "not inside a verp project" in err


def test_release(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA)
    commands.cmd_issue_start(ALPHA, i, False)
    rc, _, err = run(capsys, commands.cmd_issue_release, ALPHA, i, " ", False)
    assert rc == 1 and "reason is required" in err

    assert commands.cmd_issue_release(ALPHA, i, "out of scope", False) == 0
    issue = get(i)
    assert issue.project is None and issue.status == IssueStatus.OPEN
    assert events(i)[-1].message == "out of scope"

    rc, _, err = run(
        capsys, commands.cmd_issue_release, NO_PROJECT, i, "x", False
    )
    assert rc == 1 and "already in the backlog" in err


# --- assign ----------------------------------------------------------------


def test_assign_hands_off_and_locks_out_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    i = add(ALPHA, "t", "self-contained brief")
    commands.cmd_issue_start(ALPHA, i, False)
    assert commands.cmd_issue_assign(ALPHA, i, "beta", False) == 0
    issue = get(i)
    assert issue.project == "beta" and issue.status == IssueStatus.OPEN
    assert events(i)[-1].message == "alpha -> beta"

    assert run(capsys, commands.cmd_issue_log, ALPHA, i, "more", False)[0] == 1
    assert run(capsys, commands.cmd_issue_log, BETA, i, "mine", False)[0] == 0


def test_assign_unknown_or_same_project(
    capsys: pytest.CaptureFixture[str],
) -> None:
    i = add(ALPHA)
    rc, _, err = run(capsys, commands.cmd_issue_assign, ALPHA, i, "nope", False)
    assert rc == 1 and "no project named 'nope'" in err
    rc, _, err = run(
        capsys, commands.cmd_issue_assign, ALPHA, i, "alpha", False
    )
    assert rc == 1 and "already belongs" in err


@pytest.mark.parametrize(
    "title, body",
    [
        ("t", "see /work/alpha/api for details"),
        ("t", "branch me/alpha has the fix"),
        ("fix /work/alpha", ""),
    ],
    ids=["path-in-body", "branch-in-body", "path-in-title"],
)
def test_assign_rejects_leaky_brief(
    capsys: pytest.CaptureFixture[str], title: str, body: str
) -> None:
    i = add(ALPHA, title, body)
    rc, _, err = run(capsys, commands.cmd_issue_assign, ALPHA, i, "beta", False)
    assert rc == 1 and "self-contained" in err
    assert (
        run(capsys, commands.cmd_issue_assign, ALPHA, i, "beta", True)[0] == 1
    )
    assert get(i).project == "alpha"


def test_cli_can_force_leaky_assign(capsys: pytest.CaptureFixture[str]) -> None:
    i = add(ALPHA, "t", "see /work/alpha")
    assert run(capsys, commands.cmd_issue_assign, CLI, i, "beta", False)[0] == 1
    assert run(capsys, commands.cmd_issue_assign, CLI, i, "beta", True)[0] == 0


def test_handoff_violations_ignores_backlog() -> None:
    i = add(CLI, "t", "/work/alpha", backlog=True)
    assert identity.handoff_violations(get(i)) == []


# --- dependencies ----------------------------------------------------------


def dep_add(
    caller: Actor, issue_id: int, *deps: int, force: bool = False
) -> int:
    return commands.cmd_issue_dep_add(caller, issue_id, list(deps), force)


def dep_rm(
    caller: Actor, issue_id: int, *deps: int, force: bool = False
) -> int:
    return commands.cmd_issue_dep_rm(caller, issue_id, list(deps), force)


def test_dep_add_and_rm() -> None:
    a, b, c = add(ALPHA), add(ALPHA), add(ALPHA)
    assert dep_add(ALPHA, c, a, b) == 0
    assert get(c).depends_on == [a, b]
    assert [e.message for e in events(c)[-2:]] == [f"#{a}", f"#{b}"]
    assert events(c)[-1].kind == EventKind.DEP_ADDED

    assert dep_rm(ALPHA, c, a) == 0
    assert get(c).depends_on == [b]
    assert events(c)[-1].kind == EventKind.DEP_REMOVED


def test_dep_add_rejects_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = add(ALPHA), add(ALPHA)
    dep_add(ALPHA, b, a)
    capsys.readouterr()
    for args, msg in [
        ((b, b), "cannot depend on itself"),
        ((b, 99), "no issue 99"),
        ((b, a), "already depends on"),
        ((a, b), "would create a cycle"),
    ]:
        rc, _, err = run(capsys, dep_add, ALPHA, *args)
        assert rc == 1 and msg in err, (args, err)


def test_dep_add_rejects_transitive_cycle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    a, b, c = add(ALPHA), add(ALPHA), add(ALPHA)
    dep_add(ALPHA, b, a)
    dep_add(ALPHA, c, b)
    rc, _, err = run(capsys, dep_add, ALPHA, a, c)
    assert rc == 1 and "cycle" in err


def test_dep_add_is_all_or_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = add(ALPHA), add(ALPHA)
    assert run(capsys, dep_add, ALPHA, b, a, 99)[0] == 1
    assert get(b).depends_on == []


def test_dep_rm_missing(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = add(ALPHA), add(ALPHA)
    rc, _, err = run(capsys, dep_rm, ALPHA, b, a)
    assert rc == 1 and "does not depend on" in err


def test_dep_ownership(capsys: pytest.CaptureFixture[str]) -> None:
    mine, theirs = add(ALPHA), add(BETA)
    # Any issue may be depended on...
    assert dep_add(ALPHA, mine, theirs) == 0
    # ...but only the owner edits an issue's dependencies.
    rc, _, err = run(capsys, dep_add, BETA, mine, theirs)
    assert rc == 1 and "belongs to project 'alpha'" in err
    assert run(capsys, dep_rm, BETA, mine, theirs, force=True)[0] == 1


def test_depends_transitively(conn: sqlite3.Connection) -> None:
    a, b, c, d = (add(ALPHA) for _ in range(4))
    dep_add(ALPHA, b, a)
    dep_add(ALPHA, c, b)
    assert db.depends_transitively(conn, c, a)
    assert not db.depends_transitively(conn, a, c)
    assert not db.depends_transitively(conn, d, a)


def test_list_ready_and_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = add(ALPHA, "first"), add(ALPHA, "second")
    dep_add(ALPHA, b, a)

    def titles(readiness: str) -> list[str]:
        capsys.readouterr()
        commands.cmd_issue_list(
            ALPHA, None, False, False, None, False, True, readiness
        )
        return [i["title"] for i in json.loads(capsys.readouterr().out)]

    assert titles("ready") == ["first"]
    assert titles("blocked") == ["second"]
    close(ALPHA, a)
    assert titles("ready") == ["second"]
    assert titles("blocked") == []


def test_json_includes_blocked_by_and_dependents(
    capsys: pytest.CaptureFixture[str],
) -> None:
    a, b = add(ALPHA), add(ALPHA)
    dep_add(ALPHA, b, a)
    capsys.readouterr()
    [_, item_b] = json.loads(list_out(capsys, ALPHA, as_json=True))
    assert item_b["blocked_by"] == [a]

    _, out, _ = run(capsys, commands.cmd_issue_show, a, True)
    assert json.loads(out)["dependents"] == [b]


# --- next ------------------------------------------------------------------


def next_issue(
    capsys: pytest.CaptureFixture[str],
    caller: Actor,
    project: str | None = None,
    backlog: bool = False,
) -> Any:
    capsys.readouterr()
    assert commands.cmd_issue_next(caller, project, backlog, True) == 0
    return json.loads(capsys.readouterr().out)


def test_next_oldest_ready(capsys: pytest.CaptureFixture[str]) -> None:
    a, b, c = add(ALPHA), add(ALPHA), add(ALPHA)
    dep_add(ALPHA, a, c)
    assert next_issue(capsys, ALPHA)["id"] == b
    close(ALPHA, b)
    assert next_issue(capsys, ALPHA)["id"] == c
    close(ALPHA, c)
    assert next_issue(capsys, ALPHA)["id"] == a


def test_next_prefers_in_progress(capsys: pytest.CaptureFixture[str]) -> None:
    add(ALPHA)
    b = add(ALPHA)
    commands.cmd_issue_start(ALPHA, b, False)
    assert next_issue(capsys, ALPHA)["id"] == b


def test_next_scope(capsys: pytest.CaptureFixture[str]) -> None:
    backlog = add(CLI, backlog=True)
    theirs = add(BETA)
    assert next_issue(capsys, ALPHA) is None
    assert next_issue(capsys, NO_PROJECT)["id"] == backlog
    assert next_issue(capsys, ALPHA, project="beta")["id"] == theirs
    assert next_issue(capsys, ALPHA, backlog=True)["id"] == backlog


def test_next_none_lists_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    a = add(BETA)
    b = add(ALPHA, "waiting")
    dep_add(ALPHA, b, a)
    capsys.readouterr()
    rc, out, _ = run(capsys, commands.cmd_issue_next, ALPHA, None, False, False)
    assert rc == 0
    assert "no ready issues" in out and "waiting" in out and f"#{a}" in out


# --- deps tree -------------------------------------------------------------


def deps_tree(
    capsys: pytest.CaptureFixture[str],
    issue_id: int,
    down: bool = False,
    depth: int | None = None,
) -> Any:
    capsys.readouterr()
    assert commands.cmd_issue_deps(issue_id, down, depth, True) == 0
    return json.loads(capsys.readouterr().out)


def ids(node: Any) -> Any:
    return [node["id"], [ids(c) for c in node["children"]]]


def test_deps_tree_up_and_down(capsys: pytest.CaptureFixture[str]) -> None:
    a, b, c = add(ALPHA), add(ALPHA), add(ALPHA)
    dep_add(ALPHA, b, a)
    dep_add(ALPHA, c, b)
    assert ids(deps_tree(capsys, c)) == [c, [[b, [[a, []]]]]]
    assert ids(deps_tree(capsys, a, down=True)) == [a, [[b, [[c, []]]]]]
    assert ids(deps_tree(capsys, c, depth=1)) == [c, [[b, []]]]


def test_deps_tree_marks_repeats(capsys: pytest.CaptureFixture[str]) -> None:
    # Diamond: d -> b -> a, d -> c -> a
    a, b, c, d = (add(ALPHA) for _ in range(4))
    dep_add(ALPHA, b, a)
    dep_add(ALPHA, c, a)
    dep_add(ALPHA, d, b, c)
    tree = deps_tree(capsys, d)
    first_a = tree["children"][0]["children"][0]
    second_a = tree["children"][1]["children"][0]
    assert first_a["id"] == second_a["id"] == a
    assert not first_a["repeat"] and second_a["repeat"]


def test_deps_missing(capsys: pytest.CaptureFixture[str]) -> None:
    rc, _, err = run(capsys, commands.cmd_issue_deps, 99, False, None, False)
    assert rc == 1 and "no issue 99" in err
