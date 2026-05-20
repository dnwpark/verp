import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.table import Table

from verp.agent import format_age
from verp.db import (
    AgentStatus,
    ProjectInfo,
    SCHEMA_VERSION,
    add_project,
    add_repo_to_project,
    all_project_infos,
    clear_agent_by_prefix,
    delete_project,
    get_agent_by_prefix,
    get_all_agents,
    get_project,
    is_project_dir,
    is_repo_in_project,
    projects_using_repo,
    remove_repo_from_project,
)
from verp.git import (
    REPO_DIR,
    ahead_behind,
    branch_delete,
    branch_exists,
    branch_prefix,
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
from verp.project import setup_new
from verp.status import (
    console,
    print_repo_status,
    print_untracked_repo_status,
    short_repo_status,
    short_untracked_repo_status,
)


@dataclass
class Worktree:
    project_dir: Path
    repo: str
    path: Path


def err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def get_current_project() -> ProjectInfo | None:
    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
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

    branch = f"{branch_prefix()}{name}"
    project_dir = (Path.cwd() / name).resolve()

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


def cmd_where() -> int:
    project_info = get_current_project()
    if project_info is None:
        print("not in a verp project")
        return 1
    cwd = Path.cwd()
    project_dir = Path(project_info.path)
    rel = (
        cwd.relative_to(project_dir) if cwd.is_relative_to(project_dir) else cwd
    )
    worktree = get_current_worktree()
    print(f"project:  {project_info.name}")
    print(f"path:     {project_dir}")
    print(f"branch:   {project_info.branch}")
    if worktree:
        print(f"repo:     {worktree.repo}")
    if rel != Path("."):
        print(f"relative: {rel}")
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
            status = short_untracked_repo_status(path)
            console.print(f"    {path.name} {status}")

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


def _pull_repos(repos: list[str]) -> int:
    rc = 0
    for repo in repos:
        rp = REPO_DIR / repo
        if not rp.is_dir() or not is_git_repo(rp):
            continue
        print(f"pulling {repo}...")
        result = pull(rp)
        if result.returncode != 0:
            err(f"pull failed for {repo}:\n{result.stderr.strip()}")
            rc = 1
        else:
            output = result.stdout.strip()
            print(f"  {output if output else 'ok'}")
    return rc


def _fetch_worktrees(project_infos: list[ProjectInfo]) -> int:
    rc = 0
    for project_info in project_infos:
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


def _pull_worktree(worktree: Worktree) -> int:
    rc = _pull_repos([worktree.repo])
    print(f"fetching {worktree.project_dir.name}/{worktree.repo}...")
    result = fetch(worktree.path)
    if result.returncode != 0:
        err(f"fetch failed:\n{result.stderr.strip()}")
        return rc | 1
    print("  ok")
    return rc


def _pull_project(project_info: ProjectInfo) -> int:
    rc = _pull_repos(project_info.repos)
    rc |= _fetch_worktrees([project_info])
    return rc


def _pull_all() -> int:
    repos = (
        [
            d.name
            for d in sorted(REPO_DIR.iterdir())
            if d.is_dir() and is_git_repo(d)
        ]
        if REPO_DIR.exists()
        else []
    )
    return _pull_repos(repos) | _fetch_worktrees(all_project_infos())


def cmd_pull(all: bool = False, project: bool = False) -> int:
    if all:
        return _pull_all()

    if project:
        project_info = get_current_project()
        if project_info is None:
            err("--project requires a verp project directory")
            return 1
        return _pull_project(project_info)

    # Location-driven
    worktree = get_current_worktree()
    if worktree is not None:
        return _pull_worktree(worktree)

    project_info = get_current_project()
    if project_info is not None:
        return _pull_project(project_info)

    return _pull_all()


def _format_directory(directory: str) -> str:
    from verp.agent import directory_parts

    parts = directory_parts(directory)
    result = ""
    if parts.project_name:
        result += f"[medium_purple1]{parts.project_name}[/medium_purple1]"
    if parts.suffix:
        result += f"[grey70]{parts.suffix}[/grey70]"
    return result


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
        if agent.status == AgentStatus.WORKING:
            color = "green"
        elif agent.status == AgentStatus.WAITING_PROMPT:
            color = "yellow"
        elif agent.status == AgentStatus.PAUSED:
            color = "grey70"
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


def cmd_agent_list() -> int:
    agents = get_all_agents()
    if not agents:
        print("no agents")
        return 0
    console.print(_build_agent_table())
    return 0


def cmd_agent_monitor() -> int:
    from verp.monitor import AgentMonitor

    AgentMonitor().run()
    return 0


def cmd_agent_clear(session_id: str) -> int:
    found = clear_agent_by_prefix(session_id)
    if not found:
        err(f"no agent matching '{session_id}'")
        return 1
    print(f"cleared {session_id}")
    return 0


def cmd_agent_focus(session_id: str) -> int:
    from verp.focus import focus_by_tty, pid_to_tty

    agent = get_agent_by_prefix(session_id)
    if agent is None:
        err(f"no agent matching '{session_id}'")
        return 1
    if agent.verp_pid is None:
        err("agent has no verp PID recorded")
        return 1
    tty = pid_to_tty(agent.verp_pid)
    if tty is None:
        err("could not determine TTY for agent")
        return 1
    if not focus_by_tty(tty):
        err("could not focus terminal")
        return 1
    return 0
