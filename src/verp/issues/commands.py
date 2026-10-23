import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

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


def _issue_json(
    conn: sqlite3.Connection, issue: Issue, *, dependents: bool = False
) -> dict[str, Any]:
    data = asdict(issue)
    data["blocked_by"] = db.open_deps(conn, issue.id)
    if dependents:
        data["dependents"] = db.dependents(conn, issue.id)
    return data


def _issue_line(issue: Issue, blocked_by: list[int]) -> str:
    extra = ""
    if blocked_by:
        ids = ", ".join(f"#{d}" for d in blocked_by)
        extra = f"  [grey70](blocked by {ids})[/grey70]"
    return (
        f"[grey70]#{issue.id:<4}[/grey70] {_status(issue.status, 11)}"
        f" {escape(issue.title)}{extra}"
    )


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
        blocks = [
            (d, db.get_issue(conn, d)) for d in db.dependents(conn, issue_id)
        ]
        data = _issue_json(conn, issue, dependents=True)

    if as_json:
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
    for label, related in (("depends on", deps), ("blocks", blocks)):
        if related:
            parts = [
                f"#{d} ({_status(r.status) if r else '[red]missing[/red]'})"
                for d, r in related
            ]
            console.print(f"[grey70]{label}:[/grey70] {', '.join(parts)}")
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
    readiness: str | None = None,
) -> int:
    """readiness: None, "ready" (not closed, no open deps) or "blocked"."""
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
        blocked = {i.id: db.open_deps(conn, i.id) for i in issues}
        if readiness == "ready":
            issues = [
                i for i in issues if not i.status.closed and not blocked[i.id]
            ]
        elif readiness == "blocked":
            issues = [i for i in issues if blocked[i.id]]
        data = [_issue_json(conn, i) for i in issues]

    if as_json:
        print(json.dumps(data, indent=2))
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
        console.print(f"    {_issue_line(issue, blocked[issue.id])}")
    return 0


def cmd_issue_next(
    caller: Actor, project: str | None, backlog: bool, as_json: bool
) -> int:
    """The issue to work on next: an in-progress issue (resume) first, then
    the oldest open issue whose dependencies are all closed."""
    if project is None and not backlog:
        project = caller.project
        backlog = project is None
    with db.connect() as conn:
        issues = db.list_issues(
            conn,
            project=project,
            backlog=backlog,
            statuses=[IssueStatus.IN_PROGRESS, IssueStatus.OPEN],
        )
        blocked = {i.id: db.open_deps(conn, i.id) for i in issues}
        ready = [i for i in issues if not blocked[i.id]]
        ready.sort(key=lambda i: (i.status != IssueStatus.IN_PROGRESS, i.id))
        pick = ready[0] if ready else None
        data = _issue_json(conn, pick) if pick else None

    if as_json:
        print(json.dumps(data, indent=2))
        return 0
    if pick is None:
        waiting = [i for i in issues if blocked[i.id]]
        print("no ready issues")
        for i in waiting:
            console.print(f"  {_issue_line(i, blocked[i.id])}")
        return 0
    console.print(_issue_line(pick, []))
    return 0


def _dep_tree(
    conn: sqlite3.Connection,
    issue_id: int,
    down: bool,
    depth: int | None,
    seen: set[int],
) -> dict[str, Any]:
    issue = db.get_issue(conn, issue_id)
    node: dict[str, Any] = {
        "id": issue_id,
        "title": issue.title if issue else None,
        "status": str(issue.status) if issue else None,
        "children": [],
        "repeat": issue_id in seen,
    }
    if issue is None or node["repeat"] or depth == 0:
        return node
    seen.add(issue_id)
    related = db.dependents(conn, issue_id) if down else issue.depends_on
    next_depth = None if depth is None else depth - 1
    node["children"] = [
        _dep_tree(conn, r, down, next_depth, seen) for r in related
    ]
    return node


def _print_tree(node: dict[str, Any], indent: int) -> None:
    pad = "  " * indent
    if node["status"] is None:
        console.print(f"{pad}[red]#{node['id']} missing[/red]")
        return
    status = _status(IssueStatus(node["status"]), 11)
    repeat = "  [grey50](see above)[/grey50]" if node["repeat"] else ""
    console.print(
        f"{pad}[grey70]#{node['id']:<4}[/grey70] {status}"
        f" {escape(node['title'])}{repeat}"
    )
    for child in node["children"]:
        _print_tree(child, indent + 1)


def cmd_issue_deps(
    issue_id: int, down: bool, depth: int | None, as_json: bool
) -> int:
    """Dependency tree: what issue_id waits on (default), or with down=True
    what waits on it."""
    with db.connect() as conn:
        if db.get_issue(conn, issue_id) is None:
            return _err(f"no issue {issue_id}")
        tree = _dep_tree(conn, issue_id, down, depth, set())
    if as_json:
        print(json.dumps(tree, indent=2))
    else:
        _print_tree(tree, 0)
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
            db.add_event(conn, issue_id, caller, EventKind.STARTED)
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
            # The outcome is also kept in the history, so it survives reopen.
            kind = EventKind.DROPPED if dropped else EventKind.DONE
            db.add_event(conn, issue_id, caller, kind, text)
    print(f"closed #{issue_id} ({status})")
    return 0


def cmd_issue_reopen(caller: Actor, issue_id: int, reason: str) -> int:
    """Operator only. Returns a closed issue to `open` under the same owner;
    the previous outcome remains in the history."""
    if caller.is_agent:
        return _err("only the operator can reopen issues")
    if not reason.strip():
        return _err("a reason is required")
    with db.connect() as conn:
        issue = db.get_issue(conn, issue_id)
        if issue is None:
            return _err(f"no issue {issue_id}")
        if not issue.status.closed:
            return _err(f"issue {issue_id} is {issue.status}, not closed")
        with conn:
            db.set_status(conn, issue_id, IssueStatus.OPEN)
            db.add_event(
                conn, issue_id, caller, EventKind.REOPENED, reason.strip()
            )
    print(f"reopened #{issue_id}")
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
            db.release_issue(conn, issue_id, caller, reason)
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


def cmd_issue_dep_add(
    caller: Actor, issue_id: int, dep_ids: list[int], force: bool
) -> int:
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        for dep_id in dep_ids:
            if dep_id == issue_id:
                return _err(f"issue {issue_id} cannot depend on itself")
            if db.get_issue(conn, dep_id) is None:
                return _err(f"no issue {dep_id}")
            if dep_id in issue.depends_on:
                return _err(f"issue {issue_id} already depends on {dep_id}")
            if db.depends_transitively(conn, dep_id, issue_id):
                return _err(
                    f"issue {dep_id} already depends on {issue_id}; "
                    "adding this would create a cycle"
                )
        with conn:
            for dep_id in dep_ids:
                db.add_dep(conn, issue_id, dep_id)
                db.add_event(
                    conn, issue_id, caller, EventKind.DEP_ADDED, f"#{dep_id}"
                )
    print(f"#{issue_id} now depends on {', '.join(f'#{d}' for d in dep_ids)}")
    return 0


def cmd_issue_dep_rm(
    caller: Actor, issue_id: int, dep_ids: list[int], force: bool
) -> int:
    with db.connect() as conn:
        issue = _load_for_modify(conn, issue_id, caller, force)
        if isinstance(issue, str):
            return _err(issue)
        for dep_id in dep_ids:
            if dep_id not in issue.depends_on:
                return _err(f"issue {issue_id} does not depend on {dep_id}")
        with conn:
            for dep_id in dep_ids:
                db.remove_dep(conn, issue_id, dep_id)
                db.add_event(
                    conn, issue_id, caller, EventKind.DEP_REMOVED, f"#{dep_id}"
                )
    print(
        f"#{issue_id} no longer depends on "
        + ", ".join(f"#{d}" for d in dep_ids)
    )
    return 0
