import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from verp.agent import format_age
from verp.db import get_project
from verp.issues import db
from verp.issues.identity import (
    check_modify,
    handoff_violations,
)
from verp.issues.model import Actor, EventKind, Issue, IssueStatus

console = Console(highlight=False)

_STATUS_STYLE: dict[IssueStatus, str] = {
    IssueStatus.OPEN: "default",
    IssueStatus.IN_PROGRESS: "cyan",
    IssueStatus.DONE: "green",
    IssueStatus.DROPPED: "grey50",
}


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _read_text(value: str | None, path: str | None) -> str | None:
    """Text from an inline value or a file (`-` for stdin)."""
    if path is not None:
        if path == "-":
            return sys.stdin.read()
        return Path(path).read_text()
    return value


def _status(status: IssueStatus, width: int = 0) -> str:
    style = _STATUS_STYLE[status]
    return f"[{style}]{str(status):<{width}}[/{style}]"


def _open_deps(conn: sqlite3.Connection, issue: Issue) -> list[int]:
    out = []
    for dep_id in issue.depends_on:
        dep = db.get_issue(conn, dep_id)
        if dep is not None and not dep.status.closed:
            out.append(dep_id)
    return out


def _load_for_modify(
    conn: sqlite3.Connection,
    issue_id: int,
    caller: Actor,
    force: bool,
    *,
    allow_closed: bool = False,
) -> Issue | str:
    issue = db.get_issue(conn, issue_id)
    if issue is None:
        return f"no issue {issue_id}"
    if issue.status.closed and not allow_closed:
        if caller.is_agent or not force:
            return f"issue {issue_id} is {issue.status}"
    denied = check_modify(issue, caller, force)
    if denied is not None:
        return denied
    return issue


# --- read commands ---------------------------------------------------------


def cmd_issue_show(issue_id: int, as_json: bool) -> int:
    with db.connect() as conn:
        issue = db.get_issue(conn, issue_id)
        if issue is None:
            return _err(f"no issue {issue_id}")
        events = db.get_events(conn, issue_id)
        deps = [(d, db.get_issue(conn, d)) for d in issue.depends_on]

    if as_json:
        data = asdict(issue)
        data["events"] = [asdict(e) for e in events]
        print(json.dumps(data, indent=2))
        return 0

    console.print(f"[bold]#{issue.id} {escape(issue.title)}[/bold]")
    owner = (
        escape(issue.project) if issue.project else "[grey50]backlog[/grey50]"
    )
    console.print(
        f"[grey70]status:[/grey70] {_status(issue.status)}  "
        f"[grey70]project:[/grey70] {owner}"
    )
    console.print(
        f"[grey70]created {format_age(issue.created_at)}, "
        f"updated {format_age(issue.updated_at)}[/grey70]"
    )
    if deps:
        parts = [
            f"#{d} ({_status(dep.status) if dep else '[red]missing[/red]'})"
            for d, dep in deps
        ]
        console.print(f"[grey70]depends on:[/grey70] {', '.join(parts)}")
    if issue.body.strip():
        print()
        print(issue.body.rstrip())
    if issue.outcome:
        print()
        console.print("[bold]outcome[/bold]")
        print(issue.outcome.rstrip())
    if events:
        print()
        console.print("[bold]log[/bold]")
        for e in events:
            msg = f"  {escape(e.message)}" if e.message else ""
            console.print(
                f"  [grey70]{format_age(e.at):>8}  {escape(str(e.actor))}"
                f"  {e.kind}[/grey70]{msg}"
            )
    return 0


def cmd_issue_list(
    caller: Actor,
    project: str | None,
    backlog: bool,
    all_projects: bool,
    statuses: list[str] | None,
    include_closed: bool,
    as_json: bool,
) -> int:
    if project is None and not backlog and not all_projects:
        project = caller.project
    try:
        status_filter = [IssueStatus(s) for s in statuses] if statuses else None
    except ValueError as e:
        return _err(str(e))
    if status_filter is None and not include_closed:
        status_filter = [s for s in IssueStatus if not s.closed]

    with db.connect() as conn:
        issues = db.list_issues(
            conn, project=project, backlog=backlog, statuses=status_filter
        )
        blocked = {i.id: _open_deps(conn, i) for i in issues}

    if as_json:
        print(json.dumps([asdict(i) for i in issues], indent=2))
        return 0
    if not issues:
        print("no issues")
        return 0

    group: str | None = "\0"
    for issue in issues:
        if issue.project != group:
            if group != "\0":
                print()
            group = issue.project
            label = escape(group) if group else "[grey50]backlog[/grey50]"
            console.print(f"  [bold]{label}[/bold]")
        extra = ""
        if blocked[issue.id]:
            ids = ", ".join(f"#{d}" for d in blocked[issue.id])
            extra = f"  [grey70](blocked by {ids})[/grey70]"
        console.print(
            f"    [grey70]#{issue.id:<4}[/grey70] {_status(issue.status, 11)}"
            f" {escape(issue.title)}{extra}"
        )
    return 0


# --- write commands --------------------------------------------------------


def cmd_issue_add(
    caller: Actor,
    title: str,
    body: str | None,
    body_file: str | None,
    backlog: bool,
    as_json: bool,
) -> int:
    text = _read_text(body, body_file) or ""
    project = None if backlog else caller.project
    with db.connect() as conn:
        with conn:
            issue_id = db.create_issue(conn, title, text, project, caller)
    if as_json:
        print(json.dumps({"id": issue_id}))
    else:
        where = f"project '{project}'" if project else "backlog"
        print(f"created #{issue_id} in {where}")
    return 0


def cmd_issue_edit(
    caller: Actor,
    issue_id: int,
    title: str | None,
    body: str | None,
    body_file: str | None,
    force: bool,
) -> int:
    text = _read_text(body, body_file)
    if title is None and text is None:
        return _err("nothing to edit (use --title, --body or --body-file)")
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        with conn:
            db.update_content(conn, issue_id, title=title, body=text)
            changed = [n for n, v in (("title", title), ("body", text)) if v]
            db.add_event(
                conn,
                issue_id,
                caller,
                EventKind.EDITED,
                ", ".join(changed),
            )
    print(f"updated #{issue_id}")
    return 0


def cmd_issue_log(
    caller: Actor, issue_id: int, message: str, force: bool
) -> int:
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        with conn:
            db.add_event(conn, issue_id, caller, EventKind.LOG, message)
            db.touch(conn, issue_id)
    print(f"logged on #{issue_id}")
    return 0


def cmd_issue_start(caller: Actor, issue_id: int, force: bool) -> int:
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        if issue.project is None:
            return _err(
                f"issue {issue_id} is in the backlog; claim it first with "
                f"`verp issue claim {issue_id}`"
            )
        if issue.status != IssueStatus.OPEN:
            return _err(f"issue {issue_id} is {issue.status}")
        with conn:
            db.set_status(conn, issue_id, IssueStatus.IN_PROGRESS)
            db.add_event(
                conn,
                issue_id,
                caller,
                EventKind.STATUS,
                str(IssueStatus.IN_PROGRESS),
            )
    print(f"started #{issue_id}")
    return 0


def cmd_issue_close(
    caller: Actor,
    issue_id: int,
    outcome: str | None,
    outcome_file: str | None,
    dropped: bool,
    force: bool,
) -> int:
    text = (_read_text(outcome, outcome_file) or "").strip()
    if not text:
        what = "reason" if dropped else "outcome"
        return _err(
            f"an {what} is required (--outcome or --outcome-file): what was "
            "done, and anything a future agent should know"
        )
    status = IssueStatus.DROPPED if dropped else IssueStatus.DONE
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        with conn:
            db.set_status(conn, issue_id, status, outcome=text)
            db.add_event(conn, issue_id, caller, EventKind.STATUS, str(status))
    print(f"closed #{issue_id} ({status})")
    return 0


def cmd_issue_claim(caller: Actor, issue_id: int, force: bool) -> int:
    if caller.project is None:
        return _err("not inside a verp project; nothing to claim for")
    with db.connect() as conn:
        issue = db.get_issue(conn, issue_id)
        if issue is None:
            return _err(f"no issue {issue_id}")
        if issue.status.closed:
            return _err(f"issue {issue_id} is {issue.status}")
        if issue.project == caller.project:
            return _err(f"issue {issue_id} already belongs to this project")
        if issue.project is not None and (caller.is_agent or not force):
            return _err(
                f"issue {issue_id} belongs to project '{issue.project}'"
                + ("" if caller.is_agent else " (use --force to override)")
            )
        with conn:
            db.set_project(conn, issue_id, caller.project)
            db.add_event(
                conn, issue_id, caller, EventKind.CLAIMED, caller.project
            )
    print(f"claimed #{issue_id} for '{caller.project}'")
    return 0


def cmd_issue_release(
    caller: Actor, issue_id: int, reason: str, force: bool
) -> int:
    if not reason.strip():
        return _err("a reason is required")
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        if issue.project is None:
            return _err(f"issue {issue_id} is already in the backlog")
        with conn:
            db.set_project(conn, issue_id, None)
            if issue.status == IssueStatus.IN_PROGRESS:
                db.set_status(conn, issue_id, IssueStatus.OPEN)
            db.add_event(conn, issue_id, caller, EventKind.RELEASED, reason)
    print(f"released #{issue_id} to the backlog")
    return 0


def cmd_issue_assign(
    caller: Actor, issue_id: int, target: str, force: bool
) -> int:
    if get_project(target) is None:
        return _err(f"no project named '{target}'")
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        if issue.project == target:
            return _err(f"issue {issue_id} already belongs to '{target}'")
        hits = handoff_violations(issue)
        if hits and (caller.is_agent or not force):
            listed = "\n".join(f"  {h}" for h in hits)
            return _err(
                f"issue {issue_id} references its current project; the brief "
                "must be self-contained for the receiving agent. Remove:\n"
                + listed
            )
        with conn:
            db.set_project(conn, issue_id, target)
            if issue.status == IssueStatus.IN_PROGRESS:
                db.set_status(conn, issue_id, IssueStatus.OPEN)
            source = issue.project or "backlog"
            db.add_event(
                conn,
                issue_id,
                caller,
                EventKind.ASSIGNED,
                f"{source} -> {target}",
            )
    print(f"assigned #{issue_id} to '{target}'")
    return 0
