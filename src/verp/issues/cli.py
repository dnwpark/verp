import argparse
from typing import Any

from verp.issues.commands import (
    cmd_issue_add,
    cmd_issue_assign,
    cmd_issue_claim,
    cmd_issue_close,
    cmd_issue_edit,
    cmd_issue_list,
    cmd_issue_log,
    cmd_issue_release,
    cmd_issue_show,
    cmd_issue_start,
)
from verp.issues.identity import resolve_caller
from verp.issues.model import IssueStatus


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
    _add_json(p)

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
        )
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
    if c == "claim":
        return cmd_issue_claim(caller, args.id, args.force)
    if c == "release":
        return cmd_issue_release(caller, args.id, args.reason, args.force)
    if c == "assign":
        return cmd_issue_assign(caller, args.id, args.project, args.force)
    raise AssertionError(c)
