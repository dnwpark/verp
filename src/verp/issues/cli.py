import argparse
from typing import Any

from verp.issues.commands import (
    cmd_issue_add,
    cmd_issue_assign,
    cmd_issue_claim,
    cmd_issue_close,
    cmd_issue_dep_add,
    cmd_issue_dep_rm,
    cmd_issue_deps,
    cmd_issue_edit,
    cmd_issue_list,
    cmd_issue_log,
    cmd_issue_next,
    cmd_issue_release,
    cmd_issue_reopen,
    cmd_issue_show,
    cmd_issue_start,
)
from verp.issues import db
from verp.issues.identity import resolve_caller
from verp.issues.model import IssueStatus
from verp.issues.orphans import reconcile_orphans


def _add_force(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--force",
        action="store_true",
        help="operator override of ownership rules (not available to agents)",
    )


def _add_json(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="output JSON")


def register(sub: "argparse._SubParsersAction[Any]") -> None:
    p_issue = sub.add_parser("issue", help="track issues")
    isub = p_issue.add_subparsers(dest="issue_command", required=True)

    p = isub.add_parser("add", help="create an issue in the current project")
    p.add_argument("title")
    body = p.add_mutually_exclusive_group()
    body.add_argument("--body")
    body.add_argument("--body-file", help="read body from file ('-' = stdin)")
    p.add_argument(
        "--backlog", action="store_true", help="create unowned, in the backlog"
    )
    _add_json(p)

    p = isub.add_parser("show", help="show an issue")
    p.add_argument("id", type=int)
    _add_json(p)

    p = isub.add_parser(
        "list", help="list open issues (default: current project)"
    )
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--project", help="issues of this project")
    scope.add_argument("--backlog", action="store_true", help="unowned issues")
    scope.add_argument("--all", action="store_true", help="all projects")
    p.add_argument(
        "--status",
        action="append",
        choices=[str(s) for s in IssueStatus],
        help="filter by status (repeatable)",
    )
    p.add_argument(
        "--closed", action="store_true", help="include done/dropped issues"
    )
    readiness = p.add_mutually_exclusive_group()
    readiness.add_argument(
        "--ready",
        dest="readiness",
        action="store_const",
        const="ready",
        help="only issues whose dependencies are all closed",
    )
    readiness.add_argument(
        "--blocked",
        dest="readiness",
        action="store_const",
        const="blocked",
        help="only issues waiting on open dependencies",
    )
    _add_json(p)

    p = isub.add_parser(
        "next",
        help="the issue to work on next (in progress first, then oldest ready)",
    )
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--project", help="for this project")
    scope.add_argument(
        "--backlog", action="store_true", help="from the backlog"
    )
    _add_json(p)

    p = isub.add_parser("deps", help="show an issue's dependency tree")
    p.add_argument("id", type=int)
    p.add_argument(
        "--down",
        action="store_true",
        help="show what depends on this issue instead",
    )
    p.add_argument("--depth", type=int, help="maximum depth")
    _add_json(p)

    p = isub.add_parser("dep", help="add or remove dependencies")
    dsub = p.add_subparsers(dest="dep_command", required=True)
    for name, help_text in (
        ("add", "make an issue depend on others"),
        ("rm", "remove dependencies"),
    ):
        dp = dsub.add_parser(name, help=help_text)
        dp.add_argument("id", type=int, help="the dependent issue")
        dp.add_argument(
            "deps", type=int, nargs="+", help="issues it depends on"
        )
        _add_force(dp)

    p = isub.add_parser("edit", help="edit an issue's title or body")
    p.add_argument("id", type=int)
    p.add_argument("--title")
    body = p.add_mutually_exclusive_group()
    body.add_argument("--body")
    body.add_argument("--body-file", help="read body from file ('-' = stdin)")
    _add_force(p)

    p = isub.add_parser("log", help="append a progress note")
    p.add_argument("id", type=int)
    p.add_argument("message")
    _add_force(p)

    p = isub.add_parser("start", help="mark an issue in progress")
    p.add_argument("id", type=int)
    _add_force(p)

    p = isub.add_parser("close", help="close an issue with an outcome")
    p.add_argument("id", type=int)
    outcome = p.add_mutually_exclusive_group()
    outcome.add_argument("--outcome")
    outcome.add_argument(
        "--outcome-file", help="read outcome from file ('-' = stdin)"
    )
    p.add_argument(
        "--dropped", action="store_true", help="close as dropped, not done"
    )
    _add_force(p)

    p = isub.add_parser("reopen", help="reopen a closed issue (operator only)")
    p.add_argument("id", type=int)
    p.add_argument("reason")

    p = isub.add_parser(
        "claim", help="take a backlog issue for the current project"
    )
    p.add_argument("id", type=int)
    _add_force(p)

    p = isub.add_parser("release", help="return an issue to the backlog")
    p.add_argument("id", type=int)
    p.add_argument("reason")
    _add_force(p)

    p = isub.add_parser("assign", help="hand an issue off to another project")
    p.add_argument("id", type=int)
    p.add_argument("project")
    _add_force(p)


def dispatch(args: argparse.Namespace) -> int:
    c = args.issue_command
    caller = resolve_caller()
    with db.connect() as conn:
        reconcile_orphans(conn, caller)
    if c == "add":
        return cmd_issue_add(
            caller,
            args.title,
            args.body,
            args.body_file,
            args.backlog,
            args.json,
        )
    if c == "show":
        return cmd_issue_show(args.id, args.json)
    if c == "list":
        return cmd_issue_list(
            caller,
            args.project,
            args.backlog,
            args.all,
            args.status,
            args.closed,
            args.json,
            args.readiness,
        )
    if c == "next":
        return cmd_issue_next(caller, args.project, args.backlog, args.json)
    if c == "deps":
        return cmd_issue_deps(args.id, args.down, args.depth, args.json)
    if c == "dep":
        fn = (
            cmd_issue_dep_add if args.dep_command == "add" else cmd_issue_dep_rm
        )
        return fn(caller, args.id, args.deps, args.force)
    if c == "edit":
        return cmd_issue_edit(
            caller, args.id, args.title, args.body, args.body_file, args.force
        )
    if c == "log":
        return cmd_issue_log(caller, args.id, args.message, args.force)
    if c == "start":
        return cmd_issue_start(caller, args.id, args.force)
    if c == "close":
        return cmd_issue_close(
            caller,
            args.id,
            args.outcome,
            args.outcome_file,
            args.dropped,
            args.force,
        )
    if c == "reopen":
        return cmd_issue_reopen(caller, args.id, args.reason)
    if c == "claim":
        return cmd_issue_claim(caller, args.id, args.force)
    if c == "release":
        return cmd_issue_release(caller, args.id, args.reason, args.force)
    if c == "assign":
        return cmd_issue_assign(caller, args.id, args.project, args.force)
    raise AssertionError(c)
