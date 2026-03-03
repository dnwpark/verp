#!/usr/bin/env python3
import argparse
import argcomplete
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from dataclasses import dataclass, asdict


@dataclass
class ProjectInfo:
    name: str
    path: str
    branch: str
    repos: list[str]


@dataclass
class Worktree:
    project_dir: Path
    repo: str
    path: Path

REPO_DIR = Path.home() / ".local" / "share" / "verp" / "repos"
DATA_DIR = Path.home() / ".local" / "share" / "verp"
BRANCH_PREFIX = "dnwpark"


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def meta_path(name: str) -> Path:
    return DATA_DIR / f"{name}.json"


def load_project_info(name: str) -> ProjectInfo | None:
    p = meta_path(name)
    if p.exists():
        return ProjectInfo(**json.loads(p.read_text()))
    return None


def save_project_info(name: str, project_info: ProjectInfo) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    meta_path(name).write_text(json.dumps(asdict(project_info), indent=2) + "\n")


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
        result = run(["git", "rev-parse", "--git-dir"], cwd=rp, check=False)
        if result.returncode != 0:
            err(f"'{repo}' is not a git repository")
            return 1
        repo_paths.append(rp)

    project_dir.mkdir(parents=True)
    print(f"created {project_dir}")

    worktrees: list[str] = []
    for repo, rp in zip(repos, repo_paths):
        worktree_dir = project_dir / repo
        result = run(
            ["git", "worktree", "add", "-b", branch, str(worktree_dir)],
            cwd=rp,
            check=False,
        )
        if result.returncode != 0:
            err(f"failed to create worktree for '{repo}':\n{result.stderr.strip()}")
            for done_repo in worktrees:
                run(["git", "worktree", "remove", "--force", str(project_dir / done_repo)], cwd=REPO_DIR / done_repo, check=False)
            project_dir.rmdir()
            return 1
        print(f"  {repo}: worktree at {worktree_dir} (branch {branch})")
        worktrees.append(repo)

    save_project_info(name, ProjectInfo(name=name, path=str(project_dir), branch=branch, repos=repos))
    return 0


def all_project_paths() -> list[Path]:
    if not DATA_DIR.exists():
        return []
    return [Path(json.loads(mf.read_text())["path"]) for mf in DATA_DIR.glob("*.json")]


def is_project_dir(path: Path) -> bool:
    return path.resolve() in {p.resolve() for p in all_project_paths()}


def get_project_dir() -> Path | None:
    project_paths = {p.resolve() for p in all_project_paths()}
    return next(
        (p for p in [Path.cwd(), *Path.cwd().parents] if p.resolve() in project_paths),
        None,
    )


def get_current_worktree() -> Worktree | None:
    result = run(["git", "rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return None
    wt = Path(result.stdout.strip()).resolve()
    for project_path in all_project_paths():
        if wt.parent.resolve() == project_path.resolve():
            return Worktree(project_dir=project_path, repo=wt.name, path=wt)
    return None


def cmd_add(repo: str) -> int:
    project_dir = get_project_dir()
    if project_dir is None:
        err(f"not inside a verp project")
        return 1

    name = project_dir.name
    project_info = load_project_info(name)
    if project_info is None:
        err(f"no project named '{name}'")
        return 1
    if repo in project_info.repos:
        err(f"'{repo}' is already associated with project '{name}'")
        return 1

    rp = REPO_DIR / repo
    if not rp.is_dir():
        err(f"repo '{repo}' not found in {REPO_DIR}")
        return 1
    result = run(["git", "rev-parse", "--git-dir"], cwd=rp, check=False)
    if result.returncode != 0:
        err(f"'{repo}' is not a git repository")
        return 1

    branch = project_info.branch
    worktree_dir = project_dir / repo
    result = run(
        ["git", "worktree", "add", "-b", branch, str(worktree_dir)],
        cwd=rp,
        check=False,
    )
    if result.returncode != 0:
        err(f"failed to create worktree for '{repo}':\n{result.stderr.strip()}")
        return 1

    print(f"{repo}: worktree at {worktree_dir} (branch {branch})")
    project_info.repos.append(repo)
    save_project_info(name, project_info)
    return 0


def primary_branch(repo_path: Path) -> str | None:
    result = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo_path, check=False)
    if result.returncode != 0:
        return None
    return str(result.stdout.strip().removeprefix("origin/"))


def ahead_behind(ref_a: str, ref_b: str, cwd: Path) -> tuple[int, int] | None:
    """Returns (ahead, behind) of ref_b relative to ref_a. ahead = commits in B not in A."""
    result = run(["git", "rev-list", "--left-right", "--count", f"{ref_a}...{ref_b}"], cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    left, right = result.stdout.strip().split()
    return int(right), int(left)  # (ahead, behind)


def fmt_sync(ahead: int, behind: int) -> str:
    if ahead == 0 and behind == 0:
        return "up to date"
    parts = []
    if ahead:
        parts.append(f"{ahead} ahead")
    if behind:
        parts.append(f"{behind} behind")
    return ", ".join(parts)


def cmd_status() -> int:
    project_dir = get_project_dir()
    if project_dir is None:
        err(f"not inside a verp project")
        return 1

    project_info = load_project_info(project_dir.name)
    if project_info is None:
        err(f"no project named '{project_dir.name}'")
        return 1
    branch = project_info.branch
    repos = project_info.repos

    for i, repo in enumerate(repos):
        if i:
            print()
        wt = project_dir / repo
        rp = REPO_DIR / repo
        print(f"  {repo}")

        if not wt.is_dir():
            print(f"    worktree missing")
            continue

        primary = primary_branch(rp)
        if not primary:
            print(f"    primary branch unknown")
            continue

        local_lines = []
        remote_lines = []

        # Branch vs primary
        sync = ahead_behind(f"origin/{primary}", "HEAD", wt)
        if sync is not None:
            ahead, behind = sync
            if ahead:
                local_lines.append(f"{ahead} commit{'s' if ahead != 1 else ''} ahead of {primary}")
            if behind:
                local_lines.append(f"{behind} commit{'s' if behind != 1 else ''} behind {primary}")

        # Uncommitted changes
        result = run(["git", "status", "--porcelain"], cwd=wt, check=False)
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            changed   = sum(1 for l in lines if l[:2] != '??')
            untracked = sum(1 for l in lines if l[:2] == '??')
            if changed:
                local_lines.append(f"{changed} modified")
            if untracked:
                local_lines.append(f"{untracked} untracked")

        # Primary branch vs origin
        sync = ahead_behind(f"origin/{primary}", primary, rp)
        if sync is not None:
            ahead, behind = sync
            if ahead and behind:
                remote_lines.append(f"{primary} is out of sync with origin")
            elif behind:
                remote_lines.append(f"{primary} out of date, needs pull")
            elif ahead:
                remote_lines.append(f"{primary} out of date, needs push")

        # Worktree branch vs its origin counterpart
        sync = ahead_behind(f"origin/{branch}", "HEAD", wt)
        if sync is not None:
            ahead, behind = sync
            if ahead and behind:
                remote_lines.append(f"branch is out of sync with origin")
            elif ahead:
                remote_lines.append(f"branch out of date, needs push")
            elif behind:
                remote_lines.append(f"branch out of date, needs pull")
        else:
            remote_lines.append(f"branch not pushed to origin")

        for line in local_lines:
            print(f"    {line}")
        if remote_lines:
            if local_lines:
                print()
            for line in remote_lines:
                print(f"    {line}")

    return 0


def cmd_delete() -> int:
    project_dir = get_project_dir()
    if project_dir is None:
        err("not inside a verp project")
        return 1
    name = project_dir.name
    project_info = load_project_info(name)
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

        # Uncommitted changes
        result = run(["git", "status", "--porcelain"], cwd=wt, check=False)
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.splitlines()
            changed   = sum(1 for l in lines if l[:2] != '??')
            untracked = sum(1 for l in lines if l[:2] == '??')
            parts = []
            if changed:   parts.append(f"{changed} modified")
            if untracked: parts.append(f"{untracked} untracked")
            warnings.append(f"{repo}: uncommitted changes ({', '.join(parts)})")

        # Unpushed commits
        sync = ahead_behind(f"origin/{branch}", "HEAD", wt)
        if sync is None:
            warnings.append(f"{repo}: branch not pushed to origin")
        else:
            ahead, _ = sync
            if ahead:
                warnings.append(f"{repo}: {ahead} unpushed commit{'s' if ahead != 1 else ''}")

    # Non-repo entries in the project dir
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
            result = run(["git", "worktree", "remove", "--force", str(wt)], cwd=rp, check=False)
            if result.returncode != 0:
                err(f"failed to remove worktree for {repo}: {result.stderr.strip()}")
                return 1
        result = run(["git", "branch", "-D", branch], cwd=rp, check=False)
        if result.returncode != 0:
            err(f"failed to delete branch {branch} in {repo}: {result.stderr.strip()}")
            return 1

    shutil.rmtree(project_dir)
    meta_path(name).unlink(missing_ok=True)
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
    cmd = ["git", "rebase"]
    if interactive:
        cmd.append("-i")
    cmd.append(f"origin/{primary}")
    return subprocess.run(cmd, cwd=worktree.path).returncode


def cmd_push(force: bool) -> int:
    worktree = get_current_worktree()
    if worktree is None:
        err("not inside a verp project worktree")
        return 1
    result = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree.path, check=False)
    if result.returncode != 0:
        err("could not determine current branch")
        return 1
    branch = result.stdout.strip()
    cmd = ["git", "push", "-u", "origin", branch]
    if force:
        cmd.append("--force-with-lease")
    return subprocess.run(cmd, cwd=worktree.path).returncode


def cmd_list() -> int:
    if not DATA_DIR.exists():
        print("no projects found")
        return 0

    meta_files = sorted(DATA_DIR.glob("*.json"))
    if not meta_files:
        print("no projects found")
        return 0

    for mf in meta_files:
        project_info = ProjectInfo(**json.loads(mf.read_text()))
        name = project_info.name
        branch = project_info.branch
        repos = project_info.repos
        project_dir = Path(project_info.path)
        print(f"{name}  [{branch}]")
        for repo in repos:
            wt = project_dir / repo
            status = "ok" if wt.is_dir() else "missing"
            print(f"  {repo}: {wt}  ({status})")

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
        result = run(["git", "rev-parse", "--git-dir"], cwd=rp, check=False)
        if result.returncode != 0:
            continue

        primary = primary_branch(rp) or "?"

        url_result = run(["git", "remote", "get-url", "origin"], cwd=rp, check=False)
        url = url_result.stdout.strip() if url_result.returncode == 0 else "?"

        wt_result = run(["git", "worktree", "list", "--porcelain"], cwd=rp, check=False)
        worktree_count = wt_result.stdout.count("worktree ") - 1 if wt_result.returncode == 0 else 0

        print(f"  {rp.name}")
        print(f"    branch:    {primary}")
        print(f"    remote:    {url}")
        if worktree_count > 0:
            print(f"    worktrees: {worktree_count}")

    return 0


def cmd_repo_clone(url: str) -> int:
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    return subprocess.run(["git", "clone", url], cwd=REPO_DIR).returncode


def cmd_pull() -> int:
    rc = 0

    # Pull all primary repos
    if REPO_DIR.exists():
        for rp in sorted(REPO_DIR.iterdir()):
            if not rp.is_dir():
                continue
            result = run(["git", "rev-parse", "--git-dir"], cwd=rp, check=False)
            if result.returncode != 0:
                continue
            print(f"pulling {rp.name}...")
            result = run(["git", "pull", "--ff-only"], cwd=rp, check=False)
            if result.returncode != 0:
                err(f"pull failed for {rp.name}:\n{result.stderr.strip()}")
                rc = 1
            else:
                output = result.stdout.strip()
                print(f"  {output if output else 'ok'}")

    # Fetch in all project worktrees
    if DATA_DIR.exists():
        for mf in sorted(DATA_DIR.glob("*.json")):
            project_info = ProjectInfo(**json.loads(mf.read_text()))
            name = project_info.name
            project_dir = Path(project_info.path)
            for repo in project_info.repos:
                wt = project_dir / repo
                if not wt.is_dir():
                    err(f"worktree missing: {wt}")
                    rc = 1
                    continue
                print(f"fetching {name}/{repo}...")
                result = run(["git", "fetch"], cwd=wt, check=False)
                if result.returncode != 0:
                    err(f"fetch failed:\n{result.stderr.strip()}")
                    rc = 1
                else:
                    print("  ok")

    return rc


def main() -> None:
    description = textwrap.dedent("""\
        global:
          new <name> [repos...]    create a new project in the current directory
          list                     list all projects
          pull                     pull repos and fetch worktrees
          repo                     manage git repos

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
    sub = parser.add_subparsers(dest="command", required=True, title="_commands_")
    # Remove the subparsers group from help — the description above already lists them
    parser._action_groups = [g for g in parser._action_groups if g.title != "_commands_"]

    def repo_completer(**kwargs: object) -> list[str]:
        return [d.name for d in REPO_DIR.iterdir() if d.is_dir()]

    p_new = sub.add_parser("new", help="create a new project")
    p_new.add_argument("name", help="project name")
    p_new.add_argument("repos", nargs="*", help="repos to include")

    sub.add_parser("list", help="list all projects")
    sub.add_parser("pull", help="pull repos and fetch worktrees")
    sub.add_parser("status", help="show git status of current project")

    sub.add_parser("delete", help="delete the current project and its worktrees")

    p_rebase = sub.add_parser("rebase", help="rebase current worktree onto primary branch")
    p_rebase.add_argument("-i", "--interactive", action="store_true")

    p_push = sub.add_parser("push", help="push current worktree branch to origin")
    p_push.add_argument("-f", action="store_true")

    p_add = sub.add_parser("add", help="add a repo to the current project")
    p_add.add_argument("repo", help="repo to add").completer = repo_completer  # type: ignore[attr-defined]

    p_repo = sub.add_parser("repo", help="manage repos")
    repo_sub = p_repo.add_subparsers(dest="repo_command", required=True)
    repo_sub.add_parser("list", help="list all repos")
    p_repo_clone = repo_sub.add_parser("clone", help="clone a repo")
    p_repo_clone.add_argument("url", help="git URL to clone")

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
