#!/usr/bin/env python3
import argparse
import argcomplete
import shutil
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from verp.db import (
    SCHEMA_VERSION,
    ProjectInfo,
    add_project,
    add_repo_to_project,
    all_project_infos,
    clear_agent_by_prefix,
    delete_project,
    get_all_agents,
    get_project,
    get_project_branch,
    get_project_name_by_path,
    init_db,
    is_project_dir,
    is_repo_in_project,
    project_exists,
    remove_agent,
    upsert_agent,
)
from verp.git import (
    REPO_DIR,
    ahead_behind,
    branch_delete,
    clone,
    current_branch,
    extra_git_dirs,
    fetch,
    is_git_repo,
    primary_branch,
    pull,
    push,
    rebase,
    remote_url,
    run,
    worktree_add,
    worktree_changes,
    worktree_count,
    worktree_remove,
)
from verp.agent import format_age
from verp.project import setup_new, upgrade_project
from verp.status import console, print_repo_status, print_untracked_repo_status

BRANCH_PREFIX = "dnwpark"


@dataclass
class Worktree:
    project_dir: Path
    repo: str
    path: Path


def err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def get_current_project_dir() -> Path | None:
    for p in [Path.cwd(), *Path.cwd().parents]:
        if is_project_dir(p):
            return p
    return None


def get_current_worktree() -> Worktree | None:
    result = run(["git", "rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return None
    wt = Path(result.stdout.strip()).resolve()
    project_dir = wt.parent
    if is_project_dir(project_dir):
        return Worktree(project_dir=project_dir, repo=wt.name, path=wt)
    return None


def cmd_new(name: str, repos: list[str]) -> int:
    branch = f"{BRANCH_PREFIX}/{name}"
    project_dir = Path.cwd() / name

    if project_dir.exists():
        err(f"project '{name}' already exists at {project_dir}")
        return 1

    # Validate all repos exist before creating anything
    repo_paths: list[Path] = []
    for repo in repos:
        rp = REPO_DIR / repo
        if not rp.is_dir():
            err(f"repo '{repo}' not found in {REPO_DIR}")
            return 1
        if not is_git_repo(rp):
            err(f"'{repo}' is not a git repository")
            return 1
        repo_paths.append(rp)

    project_dir.mkdir(parents=True)
    print(f"created {project_dir}")

    worktrees: list[str] = []
    for repo, rp in zip(repos, repo_paths):
        worktree_dir = project_dir / repo
        result = worktree_add(rp, branch, worktree_dir)
        if result.returncode != 0:
            err(
                f"failed to create worktree for '{repo}':\n{result.stderr.strip()}"
            )
            for done_repo in worktrees:
                worktree_remove(REPO_DIR / done_repo, project_dir / done_repo)
            project_dir.rmdir()
            return 1
        print(f"  {repo}: worktree at {worktree_dir} (branch {branch})")
        worktrees.append(repo)

    project_info = ProjectInfo(
        name=name,
        path=str(project_dir),
        branch=branch,
        repos=repos,
        version=SCHEMA_VERSION,
    )
    add_project(name, project_info)
    setup_new(project_info)
    return 0


def cmd_add(repo: str) -> int:
    project_dir = get_current_project_dir()
    if project_dir is None:
        err("not inside a verp project")
        return 1

    name = project_dir.name
    if not project_exists(name):
        err(f"no project named '{name}'")
        return 1

    if is_repo_in_project(name, repo):
        err(f"'{repo}' is already associated with project '{name}'")
        return 1

    branch = get_project_branch(name) or ""

    rp = REPO_DIR / repo
    if not rp.is_dir():
        err(f"repo '{repo}' not found in {REPO_DIR}")
        return 1
    if not is_git_repo(rp):
        err(f"'{repo}' is not a git repository")
        return 1

    worktree_dir = project_dir / repo
    result = worktree_add(rp, branch, worktree_dir)
    if result.returncode != 0:
        err(f"failed to create worktree for '{repo}':\n{result.stderr.strip()}")
        return 1

    print(f"{repo}: worktree at {worktree_dir} (branch {branch})")
    add_repo_to_project(name, repo)
    return 0


def cmd_status() -> int:
    project_dir = get_current_project_dir()
    if project_dir is None:
        err("not inside a verp project")
        return 1

    project_info = get_project(project_dir.name)
    if project_info is None:
        err(f"no project named '{project_dir.name}'")
        return 1

    printed = 0
    for repo in project_info.repos:
        if printed:
            print()
        print_repo_status(repo, project_dir, project_info.branch)
        printed += 1

    for path in extra_git_dirs(project_dir, project_info.repos):
        if printed:
            print()
        print_untracked_repo_status(path)
        printed += 1

    return 0


def cmd_delete() -> int:
    project_dir = get_current_project_dir()
    if project_dir is None:
        err("not inside a verp project")
        return 1
    name = project_dir.name
    project_info = get_project(name)
    if project_info is None:
        err(f"no project named '{name}'")
        return 1
    branch = project_info.branch
    repos = project_info.repos

    warnings = []

    for repo in repos:
        wt = project_dir / repo
        if not wt.is_dir():
            continue

        changed, untracked = worktree_changes(wt)
        if changed or untracked:
            parts = []
            if changed:
                parts.append(f"{changed} modified")
            if untracked:
                parts.append(f"{untracked} untracked")
            warnings.append(f"{repo}: uncommitted changes ({', '.join(parts)})")

        sync = ahead_behind(f"origin/{branch}", "HEAD", wt)
        if sync is None:
            warnings.append(f"{repo}: branch not pushed to origin")
        else:
            ahead, _ = sync
            if ahead:
                warnings.append(
                    f"{repo}: {ahead} unpushed commit{'s' if ahead != 1 else ''}"
                )

    known = set(repos)
    for entry in project_dir.iterdir():
        if entry.name not in known:
            kind = "directory" if entry.is_dir() else "file"
            warnings.append(f"non-repo {kind}: {entry.name}")

    if warnings:
        print(f"project '{name}' has changes:")
        for w in warnings:
            print(f"  {w}")
    else:
        print(f"project '{name}' has no changes")

    answer = input("\ndelete? [y/N] ").strip().lower()
    if answer != "y":
        print("aborted")
        return 1

    for repo in repos:
        wt = project_dir / repo
        rp = REPO_DIR / repo
        if wt.is_dir():
            result = worktree_remove(rp, wt)
            if result.returncode != 0:
                err(
                    f"failed to remove worktree for {repo}: {result.stderr.strip()}"
                )
                return 1
        result = branch_delete(rp, branch)
        if result.returncode != 0:
            err(
                f"failed to delete branch {branch} in {repo}: {result.stderr.strip()}"
            )
            return 1

    shutil.rmtree(project_dir)
    delete_project(name)
    print(f"deleted '{name}'")
    return 0


def cmd_rebase(interactive: bool) -> int:
    worktree = get_current_worktree()
    if worktree is None:
        err("not inside a verp project worktree")
        return 1
    primary = primary_branch(REPO_DIR / worktree.repo)
    if not primary:
        err(f"could not determine primary branch for {worktree.repo}")
        return 1
    return rebase(worktree.path, f"origin/{primary}", interactive)


def cmd_push(force: bool) -> int:
    worktree = get_current_worktree()
    if worktree is None:
        err("not inside a verp project worktree")
        return 1
    branch = current_branch(worktree.path)
    if branch is None:
        err("could not determine current branch")
        return 1
    return push(worktree.path, branch, force)


def cmd_list() -> int:
    projects = all_project_infos()
    if not projects:
        print("no projects found")
        return 0

    for i, project_info in enumerate(projects):
        if i:
            print()
        project_dir = Path(project_info.path)
        console.print(f"  [bold]{project_info.name}[/bold]")
        printed = 0
        for repo in project_info.repos:
            if printed:
                print()
            print_repo_status(
                repo, project_dir, project_info.branch, indent="    "
            )
            printed += 1
        for path in extra_git_dirs(project_dir, project_info.repos):
            if printed:
                print()
            print_untracked_repo_status(path, indent="    ")
            printed += 1

    return 0


def cmd_repo_list() -> int:
    if not REPO_DIR.exists():
        print("no repos")
        return 0

    repos = sorted(d for d in REPO_DIR.iterdir() if d.is_dir())
    if not repos:
        print("no repos")
        return 0

    for rp in repos:
        if not is_git_repo(rp):
            continue

        primary = primary_branch(rp) or "?"
        url = remote_url(rp) or "?"
        wt_count = worktree_count(rp)

        print(f"  {rp.name}")
        print(f"    branch:    {primary}")
        print(f"    remote:    {url}")
        if wt_count > 0:
            print(f"    worktrees: {wt_count}")

    return 0


def cmd_repo_clone(url: str) -> int:
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    return clone(url)


def cmd_pull() -> int:
    rc = 0

    # Pull all primary repos
    if REPO_DIR.exists():
        for rp in sorted(REPO_DIR.iterdir()):
            if not rp.is_dir() or not is_git_repo(rp):
                continue
            print(f"pulling {rp.name}...")
            result = pull(rp)
            if result.returncode != 0:
                err(f"pull failed for {rp.name}:\n{result.stderr.strip()}")
                rc = 1
            else:
                output = result.stdout.strip()
                print(f"  {output if output else 'ok'}")

    # Fetch in all project worktrees
    for project_info in all_project_infos():
        name = project_info.name
        project_dir = Path(project_info.path)
        for repo in project_info.repos:
            wt = project_dir / repo
            if not wt.is_dir():
                err(f"worktree missing: {wt}")
                rc = 1
                continue
            print(f"fetching {name}/{repo}...")
            result = fetch(wt)
            if result.returncode != 0:
                err(f"fetch failed:\n{result.stderr.strip()}")
                rc = 1
            else:
                print("  ok")

    return rc


def _status_color(status: str) -> str:
    if status == "working":
        return "green"
    return "dark_orange"


def cmd_agent_list() -> int:
    agents = get_all_agents()
    if not agents:
        print("no agents")
        return 0
    for agent in agents:
        sid = agent.session_id[:8]
        color = _status_color(agent.status)
        status = agent.status
        if agent.tool:
            status = f"{status} ({agent.tool})"
        console.print(
            f"  [bold]{sid}[/bold]  {agent.project}"
            f"  [{color}]{status}[/{color}]"
            f"  [grey70]{format_age(agent.updated_at)}[/grey70]"
        )
    return 0


def cmd_agent_clear(session_id: str) -> int:
    found = clear_agent_by_prefix(session_id)
    if not found:
        err(f"no agent matching '{session_id}'")
        return 1
    print(f"cleared {session_id}")
    return 0


def cmd_internal_agent_event(
    session_id: str, project_dir: str, status: str, tool: str | None
) -> int:
    project_name = (
        get_project_name_by_path(Path(project_dir)) if project_dir else None
    )
    if project_name is None:
        return 0
    upsert_agent(session_id, project_name, status, tool or None)
    return 0


def cmd_internal_agent_remove(session_id: str) -> int:
    remove_agent(session_id)
    return 0


def main() -> None:
    init_db()
    for project_info in all_project_infos():
        upgrade_project(project_info)
    description = textwrap.dedent("""\
        global:
          new <name> [repos...]    create a new project in the current directory
          list                     list all projects
          pull                     pull repos and fetch worktrees
          repo                     manage git repos
          agent                    manage agents

        project:
          status                   show git status of each worktree
          add <repo>               add a repo to the current project
          delete                   delete the current project and its worktrees

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
    sub.add_parser("pull", help="pull repos and fetch worktrees")
    sub.add_parser("status", help="show git status of current project")

    sub.add_parser(
        "delete", help="delete the current project and its worktrees"
    )

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

    p_repo = sub.add_parser("repo", help="manage repos")
    repo_sub = p_repo.add_subparsers(dest="repo_command", required=True)
    repo_sub.add_parser("list", help="list all repos")
    p_repo_clone = repo_sub.add_parser("clone", help="clone a repo")
    p_repo_clone.add_argument("url", help="git URL to clone")

    p_agent = sub.add_parser("agent", help="manage agents")
    agent_sub = p_agent.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("list", help="list all agents")
    p_agent_clear = agent_sub.add_parser("clear", help="clear an agent entry")
    p_agent_clear.add_argument("id", help="session ID prefix")

    p_internal = sub.add_parser("_internal")
    internal_sub = p_internal.add_subparsers(
        dest="internal_command", required=True
    )
    p_agent_event = internal_sub.add_parser("agent_event")
    p_agent_event.add_argument("session_id")
    p_agent_event.add_argument("project_dir")
    p_agent_event.add_argument("status")
    p_agent_event.add_argument("tool", nargs="?", default=None)
    p_agent_remove = internal_sub.add_parser("agent_remove")
    p_agent_remove.add_argument("session_id")

    argcomplete.autocomplete(parser, always_complete_options=False)
    args = parser.parse_args()

    if args.command == "new":
        sys.exit(cmd_new(args.name, args.repos))
    elif args.command == "list":
        sys.exit(cmd_list())
    elif args.command == "pull":
        sys.exit(cmd_pull())
    elif args.command == "add":
        sys.exit(cmd_add(args.repo))
    elif args.command == "status":
        sys.exit(cmd_status())
    elif args.command == "delete":
        sys.exit(cmd_delete())
    elif args.command == "rebase":
        sys.exit(cmd_rebase(args.interactive))
    elif args.command == "push":
        sys.exit(cmd_push(args.f))
    elif args.command == "repo":
        if args.repo_command == "list":
            sys.exit(cmd_repo_list())
        elif args.repo_command == "clone":
            sys.exit(cmd_repo_clone(args.url))
    elif args.command == "agent":
        if args.agent_command == "list":
            sys.exit(cmd_agent_list())
        elif args.agent_command == "clear":
            sys.exit(cmd_agent_clear(args.id))
    elif args.command == "_internal":
        if args.internal_command == "agent_event":
            sys.exit(
                cmd_internal_agent_event(
                    args.session_id, args.project_dir, args.status, args.tool
                )
            )
        elif args.internal_command == "agent_remove":
            sys.exit(cmd_internal_agent_remove(args.session_id))
