#!/usr/bin/env python3
import argparse
import signal
import sys
import textwrap

import argcomplete

from verp.claude_hooks import (
    cmd_internal_hook_permission_request,
    cmd_internal_hook_post_tool_use,
    cmd_internal_hook_post_tool_use_failure,
    cmd_internal_hook_pre_tool_use,
    cmd_internal_hook_session_end,
    cmd_internal_hook_session_start,
    cmd_internal_hook_stop,
    cmd_internal_hook_user_prompt_submit,
)
from verp.commands import (
    cmd_add,
    cmd_agent_clear,
    cmd_agent_focus,
    cmd_agent_list,
    cmd_agent_monitor,
    cmd_delete,
    cmd_list,
    cmd_new,
    cmd_pull,
    cmd_push,
    cmd_ff,
    cmd_rebase,
    cmd_remove,
    cmd_repo_clone,
    cmd_repo_list,
    cmd_repo_unclone,
    cmd_status,
    cmd_where,
)
from verp.db import all_project_infos, init_db
from verp.git import REPO_DIR
from verp.paths import DATA_DIR
from verp.project import init_project
from verp.terminal import cmd_claude


def main() -> None:
    # Ensure that we don't exit before the stop hook is fully processed.
    if len(sys.argv) > 1 and sys.argv[1] == "_claude":
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    from contextlib import closing
    from verp.claude_dir import init_claude_dir

    with closing(init_db(DATA_DIR)) as conn:
        init_claude_dir(conn)
    for project_info in all_project_infos():
        init_project(project_info)

    if len(sys.argv) > 1 and sys.argv[1] == "claude":
        sys.exit(cmd_claude(sys.argv[2:]))

    description = textwrap.dedent("""\
        global:
          new <name> [repos...]    create a new project in the current directory
          list                     list all projects
          pull                     pull repos and fetch worktrees
          repo                     manage git repos
          agent                    manage agents
          claude [args...]         launch claude with verp hooks

        project:
          status                   show git status of each worktree
          add <repo>               add a repo to the current project
          remove <repo>            remove a repo from the current project
          delete                   delete the current project and its worktrees

        worktree/project:
          ff                       fast-forward worktrees onto primary branch

        worktree:
          rebase [-i]              rebase onto the primary branch
          push [-f]                push the current branch to origin
        """)

    parser = argparse.ArgumentParser(
        prog="verp",
        usage="verp <command> [args]",
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="command", required=True, title="_commands_"
    )
    # Remove the subparsers group from help — the description above already lists them
    parser._action_groups = [
        g for g in parser._action_groups if g.title != "_commands_"
    ]

    def repo_completer(**kwargs: object) -> list[str]:
        return [d.name for d in REPO_DIR.iterdir() if d.is_dir()]

    p_new = sub.add_parser("new", help="create a new project")
    p_new.add_argument("name", help="project name")
    p_new.add_argument("repos", nargs="*", help="repos to include")

    sub.add_parser("list", help="list all projects")
    p_pull = sub.add_parser("pull", help="pull repos and fetch worktrees")
    p_pull_scope = p_pull.add_mutually_exclusive_group()
    p_pull_scope.add_argument(
        "--all",
        action="store_true",
        help="pull all repos regardless of location",
    )
    p_pull_scope.add_argument(
        "--project",
        action="store_true",
        help="pull all repos for the current project",
    )
    sub.add_parser("status", help="show git status of current project")
    sub.add_parser("where", help="show current verp project and location")

    sub.add_parser(
        "delete", help="delete the current project and its worktrees"
    )

    sub.add_parser("ff", help="fast-forward worktrees onto primary branch")

    p_rebase = sub.add_parser(
        "rebase", help="rebase current worktree onto primary branch"
    )
    p_rebase.add_argument("-i", "--interactive", action="store_true")

    p_push = sub.add_parser(
        "push", help="push current worktree branch to origin"
    )
    p_push.add_argument("-f", action="store_true")

    p_add = sub.add_parser("add", help="add a repo to the current project")
    p_add.add_argument("repo", help="repo to add").completer = repo_completer  # type: ignore[attr-defined]

    p_remove = sub.add_parser(
        "remove", help="remove a repo from the current project"
    )
    p_remove.add_argument("repo", help="repo to remove")

    p_repo = sub.add_parser("repo", help="manage repos")
    repo_sub = p_repo.add_subparsers(dest="repo_command", required=True)
    repo_sub.add_parser("list", help="list all repos")
    p_repo_clone = repo_sub.add_parser("clone", help="clone a repo")
    p_repo_clone.add_argument("url", help="git URL to clone")
    p_repo_unclone = repo_sub.add_parser(
        "unclone", help="delete a local repo clone"
    )
    p_repo_unclone.add_argument("repo", help="repo name to remove")

    p_agent = sub.add_parser("agent", help="manage agents")
    agent_sub = p_agent.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("list", help="list all agents")
    agent_sub.add_parser("monitor", help="live-updating agent monitor")
    p_agent_clear = agent_sub.add_parser("clear", help="clear an agent entry")
    p_agent_clear.add_argument("id", help="session ID prefix")
    p_agent_focus = agent_sub.add_parser(
        "focus", help="focus terminal window of an agent"
    )
    p_agent_focus.add_argument("id", help="session ID prefix")

    p_verp_claude = sub.add_parser(
        "claude", help="launch claude with verp hooks"
    )
    p_verp_claude.add_argument("args", nargs=argparse.REMAINDER)

    p_internal = sub.add_parser("_internal")
    internal_sub = p_internal.add_subparsers(
        dest="internal_command", required=True
    )
    p_agent_remove = internal_sub.add_parser("agent_remove")
    p_agent_remove.add_argument("session_id")

    p_claude = sub.add_parser("_claude")
    claude_sub = p_claude.add_subparsers(dest="claude_command", required=True)
    p_hook_session_start = claude_sub.add_parser("hook_session_start")
    p_hook_session_start.add_argument("session_id")
    p_hook_session_start.add_argument("timestamp", type=int)
    p_hook_session_end = claude_sub.add_parser("hook_session_end")
    p_hook_session_end.add_argument("session_id")
    p_hook_session_end.add_argument("timestamp", type=int)
    p_hook_pre_tool_use = claude_sub.add_parser("hook_pre_tool_use")
    p_hook_pre_tool_use.add_argument("session_id")
    p_hook_pre_tool_use.add_argument("directory")
    p_hook_pre_tool_use.add_argument("tool")
    p_hook_pre_tool_use.add_argument("timestamp", type=int)
    p_hook_post_tool_use_failure = claude_sub.add_parser(
        "hook_post_tool_use_failure"
    )
    p_hook_post_tool_use_failure.add_argument("session_id")
    p_hook_post_tool_use_failure.add_argument("directory")
    p_hook_post_tool_use_failure.add_argument("tool")
    p_hook_post_tool_use_failure.add_argument("timestamp", type=int)
    p_hook_post_tool_use = claude_sub.add_parser("hook_post_tool_use")
    p_hook_post_tool_use.add_argument("session_id")
    p_hook_post_tool_use.add_argument("directory")
    p_hook_post_tool_use.add_argument("tool")
    p_hook_post_tool_use.add_argument("timestamp", type=int)
    p_hook_permission_request = claude_sub.add_parser("hook_permission_request")
    p_hook_permission_request.add_argument("session_id")
    p_hook_permission_request.add_argument("directory")
    p_hook_permission_request.add_argument("tool")
    p_hook_permission_request.add_argument("timestamp", type=int)
    p_hook_user_prompt_submit = claude_sub.add_parser("hook_user_prompt_submit")
    p_hook_user_prompt_submit.add_argument("session_id")
    p_hook_user_prompt_submit.add_argument("directory")
    p_hook_user_prompt_submit.add_argument("timestamp", type=int)
    p_hook_stop = claude_sub.add_parser("hook_stop")
    p_hook_stop.add_argument("session_id")
    p_hook_stop.add_argument("directory")
    p_hook_stop.add_argument("timestamp", type=int)

    argcomplete.autocomplete(parser, always_complete_options=False)
    args = parser.parse_args()

    if args.command == "new":
        sys.exit(cmd_new(args.name, args.repos))
    elif args.command == "list":
        sys.exit(cmd_list())
    elif args.command == "pull":
        sys.exit(cmd_pull(all=args.all, project=args.project))
    elif args.command == "add":
        sys.exit(cmd_add(args.repo))
    elif args.command == "remove":
        sys.exit(cmd_remove(args.repo))
    elif args.command == "where":
        sys.exit(cmd_where())
    elif args.command == "status":
        sys.exit(cmd_status())
    elif args.command == "delete":
        sys.exit(cmd_delete())
    elif args.command == "ff":
        sys.exit(cmd_ff())
    elif args.command == "rebase":
        sys.exit(cmd_rebase(args.interactive))
    elif args.command == "push":
        sys.exit(cmd_push(args.f))
    elif args.command == "repo":
        if args.repo_command == "list":
            sys.exit(cmd_repo_list())
        elif args.repo_command == "clone":
            sys.exit(cmd_repo_clone(args.url))
        elif args.repo_command == "unclone":
            sys.exit(cmd_repo_unclone(args.repo))
    elif args.command == "agent":
        if args.agent_command == "list":
            sys.exit(cmd_agent_list())
        elif args.agent_command == "monitor":
            sys.exit(cmd_agent_monitor())
        elif args.agent_command == "clear":
            sys.exit(cmd_agent_clear(args.id))
        elif args.agent_command == "focus":
            sys.exit(cmd_agent_focus(args.id))
    elif args.command == "_claude":
        if args.claude_command == "hook_session_start":
            sys.exit(
                cmd_internal_hook_session_start(args.session_id, args.timestamp)
            )
        elif args.claude_command == "hook_session_end":
            sys.exit(
                cmd_internal_hook_session_end(args.session_id, args.timestamp)
            )
        elif args.claude_command == "hook_pre_tool_use":
            sys.exit(
                cmd_internal_hook_pre_tool_use(
                    args.session_id, args.directory, args.tool, args.timestamp
                )
            )
        elif args.claude_command == "hook_post_tool_use_failure":
            sys.exit(
                cmd_internal_hook_post_tool_use_failure(
                    args.session_id, args.directory, args.tool, args.timestamp
                )
            )
        elif args.claude_command == "hook_post_tool_use":
            sys.exit(
                cmd_internal_hook_post_tool_use(
                    args.session_id, args.directory, args.tool, args.timestamp
                )
            )
        elif args.claude_command == "hook_permission_request":
            sys.exit(
                cmd_internal_hook_permission_request(
                    args.session_id, args.directory, args.tool, args.timestamp
                )
            )
        elif args.claude_command == "hook_user_prompt_submit":
            sys.exit(
                cmd_internal_hook_user_prompt_submit(
                    args.session_id, args.directory, args.timestamp
                )
            )
        elif args.claude_command == "hook_stop":
            sys.exit(
                cmd_internal_hook_stop(
                    args.session_id, args.directory, args.timestamp
                )
            )
    elif args.command == "claude":
        sys.exit(cmd_claude(args.args))
