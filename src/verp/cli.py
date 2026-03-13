#!/usr/bin/env python3
import argparse
import argcomplete
import fcntl
import json
import os
import pty
import select
import signal
import socket
import subprocess
import sys
import termios
import time
import tty
import textwrap
from dataclasses import dataclass

from verp.claude_permission_hook import (
    cmd_internal_hook_permission_request,
    handle_permission_request,
)
from pathlib import Path

from verp.db import (
    DATA_DIR,
    SCHEMA_VERSION,
    ProjectInfo,
    add_project,
    add_repo_to_project,
    all_project_infos,
    clear_agent_by_prefix,
    reset_agent_tool,
    set_agent_status,
    set_agent_tool,
    delete_project,
    get_all_agents,
    get_project,
    get_project_branch,
    projects_using_repo,
    init_internal,
    is_project_dir,
    is_repo_in_project,
    project_exists,
    remove_agent,
    remove_agents_by_pid,
    remove_repo_from_project,
    set_agents_status_by_pid,
)
from verp.git import (
    REPO_DIR,
    ahead_behind,
    branch_delete,
    branch_exists,
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
from verp.project import init_project, setup_new
from rich.live import Live
from rich.table import Table

from verp.status import (
    console,
    print_repo_status,
    print_untracked_repo_status,
    short_repo_status,
)

BRANCH_PREFIX = "dnwpark"


@dataclass
class Worktree:
    project_dir: Path
    repo: str
    path: Path


def err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def get_current_project() -> ProjectInfo | None:
    for p in [Path.cwd(), *Path.cwd().parents]:
        if is_project_dir(p):
            return get_project(p.name)
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
    name = name.strip("/")
    if "/" in name:
        err(f"invalid project name '{name}': must not contain '/'")
        return 1

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
    project_info = get_current_project()
    if project_info is None:
        err("not inside a verp project")
        return 1

    name = project_info.name
    project_dir = Path(project_info.path)

    if is_repo_in_project(name, repo):
        err(f"'{repo}' is already associated with project '{name}'")
        return 1

    rp = REPO_DIR / repo
    if not rp.is_dir():
        err(f"repo '{repo}' not found in {REPO_DIR}")
        return 1
    if not is_git_repo(rp):
        err(f"'{repo}' is not a git repository")
        return 1

    worktree_dir = project_dir / repo
    result = worktree_add(rp, project_info.branch, worktree_dir)
    if result.returncode != 0:
        err(f"failed to create worktree for '{repo}':\n{result.stderr.strip()}")
        return 1

    print(f"{repo}: worktree at {worktree_dir} (branch {project_info.branch})")
    add_repo_to_project(name, repo)
    return 0


def cmd_remove(repo: str) -> int:
    project_info = get_current_project()
    if project_info is None:
        err("not inside a verp project")
        return 1

    name = project_info.name
    project_dir = Path(project_info.path)
    branch = project_info.branch

    if not is_repo_in_project(name, repo):
        err(f"'{repo}' is not associated with project '{name}'")
        return 1

    print_repo_status(repo, project_dir, branch)

    answer = input("\nremove? [y/N] ").strip().lower()
    if answer != "y":
        print("aborted")
        return 1

    wt = project_dir / repo
    rp = REPO_DIR / repo

    if wt.is_dir():
        result = worktree_remove(rp, wt)
        if result.returncode != 0:
            err(f"failed to remove worktree: {result.stderr.strip()}")
            return 1

    if branch_exists(rp, branch):
        result = branch_delete(rp, branch)
        if result.returncode != 0:
            err(f"failed to delete branch {branch}: {result.stderr.strip()}")
            return 1

    remove_repo_from_project(name, repo)
    print(f"removed '{repo}' from project '{name}'")
    return 0


def cmd_status() -> int:
    project_info = get_current_project()
    if project_info is None:
        err("not inside a verp project")
        return 1

    project_dir = Path(project_info.path)

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
    project_info = get_current_project()
    if project_info is None:
        err("not inside a verp project")
        return 1
    name = project_info.name
    project_dir = Path(project_info.path)
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

    known = set(repos) | {".claude"}
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
        if branch_exists(rp, branch):
            result = branch_delete(rp, branch)
            if result.returncode != 0:
                err(
                    f"failed to delete branch {branch} in {repo}: {result.stderr.strip()}"
                )
                return 1

    subprocess.run(["rm", "-rf", str(project_dir)], check=True)
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
        for repo in project_info.repos:
            status = short_repo_status(repo, project_dir, project_info.branch)
            console.print(f"    {repo} {status}")
        for path in extra_git_dirs(project_dir, project_info.repos):
            console.print(f"    {path.name} [grey70](untracked)[/grey70]")

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


def cmd_repo_unclone(repo: str) -> int:
    rp = REPO_DIR / repo
    if not rp.is_dir():
        err(f"repo '{repo}' not found in {REPO_DIR}")
        return 1
    using = projects_using_repo(repo)
    if using:
        err(f"repo '{repo}' is used by project(s): {', '.join(using)}")
        return 1
    import shutil

    shutil.rmtree(rp)
    print(f"removed {rp}")
    return 0


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


def _format_directory(directory: str) -> str:
    path = Path(directory)
    if is_project_dir(path):
        return f"[medium_purple1]{path.name}[/medium_purple1]"
    for p in path.parents:
        if is_project_dir(p):
            return f"[medium_purple1]{p.name}[/medium_purple1][grey70]/{path.relative_to(p)}[/grey70]"
    home = Path.home()
    try:
        return f"[grey70]~/{path.relative_to(home)}[/grey70]"
    except ValueError:
        return f"[grey70]{directory}[/grey70]"


def cmd_agent_list() -> int:
    agents = get_all_agents()
    if not agents:
        print("no agents")
        return 0
    console.print(_build_agent_table())
    return 0


def _build_agent_table() -> Table:
    agents = get_all_agents()
    table = Table(box=None, padding=(0, 2), show_header=False, highlight=False)
    table.add_column()
    table.add_column()
    table.add_column()
    table.add_column()
    if not agents:
        table.add_row("[grey70]no agents[/grey70]", "", "", "")
    for agent in agents:
        sid = agent.session_id[:8]
        if agent.status == "working":
            color = "green"
        elif agent.status == "waiting_prompt":
            color = "yellow"
        else:
            color = "dark_orange"
        status_str = (
            f"{agent.status} ({agent.tool})" if agent.tool else agent.status
        )
        table.add_row(
            f"[bold]{sid}[/bold]",
            _format_directory(agent.directory),
            f"[{color}]{status_str}[/{color}]",
            f"[grey70]{format_age(agent.updated_at)}[/grey70]",
        )
    return table


def cmd_agent_monitor() -> int:
    import time

    try:
        with Live(
            _build_agent_table(),
            console=console,
            refresh_per_second=2,
        ) as live:
            while True:
                time.sleep(0.5)
                live.update(_build_agent_table())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_agent_clear(session_id: str) -> int:
    found = clear_agent_by_prefix(session_id)
    if not found:
        err(f"no agent matching '{session_id}'")
        return 1
    print(f"cleared {session_id}")
    return 0


def cmd_internal_hook_session_start(session_id: str, timestamp: int) -> int:
    return 0


def cmd_internal_hook_session_end(session_id: str, timestamp: int) -> int:
    remove_agent(session_id)
    return 0


def cmd_internal_hook_pre_tool_use(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(session_id, directory, "working", timestamp)
    set_agent_tool(session_id, tool)
    return 0


def cmd_internal_hook_post_tool_use(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(session_id, directory, "working", timestamp)
    reset_agent_tool(session_id)
    return 0


def cmd_internal_hook_post_tool_use_failure(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(session_id, directory, "waiting_prompt", timestamp)
    reset_agent_tool(session_id)
    return 0


def cmd_internal_hook_user_prompt_submit(
    session_id: str, directory: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(session_id, directory, "working", timestamp)
    return 0


def cmd_internal_hook_stop(
    session_id: str, directory: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(session_id, directory, "waiting_prompt", timestamp)
    return 0


def _set_winsize(fd: int) -> None:
    size = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


def cmd_claude(args: list[str]) -> int:
    settings = DATA_DIR / "claude-settings.json"
    cmd = ["claude", "--settings", str(settings)] + args

    sock_path = f"/tmp/verp-{os.getpid()}.sock"
    listen_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listen_sock.bind(sock_path)
    listen_sock.listen(1)

    pid, master_fd = pty.fork()
    if pid == 0:
        listen_sock.close()
        os.environ["VERP_SOCKET"] = sock_path
        os.execvp(cmd[0], cmd)

    _set_winsize(master_fd)
    signal.signal(signal.SIGWINCH, lambda _s, _f: _set_winsize(master_fd))

    stdin_fd = sys.stdin.fileno()
    old = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    try:
        while True:
            try:
                fds, _, _ = select.select(
                    [master_fd, sys.stdin, listen_sock], [], []
                )
            except (KeyboardInterrupt, OSError):
                break
            if master_fd in fds:
                try:
                    data = os.read(master_fd, 1024)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
            if sys.stdin in fds:
                data = os.read(stdin_fd, 1024)
                if b"\x03" in data:
                    set_agents_status_by_pid(
                        os.getpid(), "waiting_prompt", int(time.time() * 1000)
                    )
                try:
                    os.write(master_fd, data)
                except OSError:
                    break
            if listen_sock in fds:
                conn, _ = listen_sock.accept()
                handle_permission_request(conn, stdin_fd)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old)
        os.close(master_fd)
        listen_sock.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        try:
            remove_agents_by_pid(os.getpid())
        except Exception:
            pass

    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def main() -> None:
    # Ensure that we don't exit before the stop hook is fully processed.
    if len(sys.argv) > 1 and sys.argv[1] == "_claude":
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    init_internal()
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
        sys.exit(cmd_pull())
    elif args.command == "add":
        sys.exit(cmd_add(args.repo))
    elif args.command == "remove":
        sys.exit(cmd_remove(args.repo))
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
        elif args.repo_command == "unclone":
            sys.exit(cmd_repo_unclone(args.repo))
    elif args.command == "agent":
        if args.agent_command == "list":
            sys.exit(cmd_agent_list())
        elif args.agent_command == "monitor":
            sys.exit(cmd_agent_monitor())
        elif args.agent_command == "clear":
            sys.exit(cmd_agent_clear(args.id))
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
