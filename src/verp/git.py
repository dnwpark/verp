import subprocess
from pathlib import Path

REPO_DIR = Path.home() / ".local" / "share" / "verp" / "repos"


def run(
    cmd: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, check=check, capture_output=True, text=True
    )


def is_git_repo(path: Path) -> bool:
    return (
        run(["git", "rev-parse", "--git-dir"], cwd=path, check=False).returncode
        == 0
    )


def primary_branch(repo_path: Path) -> str | None:
    result = run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0:
        return None
    return str(result.stdout.strip().removeprefix("origin/"))


def current_branch(path: Path) -> str | None:
    result = run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def remote_url(repo_dir: Path) -> str | None:
    result = run(
        ["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def worktree_count(repo_dir: Path) -> int:
    result = run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo_dir, check=False
    )
    return result.stdout.count("worktree ") - 1 if result.returncode == 0 else 0


def worktree_changes(path: Path) -> tuple[int, int]:
    """Returns (changed, untracked) file counts."""
    result = run(["git", "status", "--porcelain"], cwd=path, check=False)
    if result.returncode != 0:
        return 0, 0
    lines = result.stdout.splitlines()
    changed = sum(1 for l in lines if l[:2] != "??")
    untracked = sum(1 for l in lines if l[:2] == "??")
    return changed, untracked


def ahead_behind(ref_a: str, ref_b: str, cwd: Path) -> tuple[int, int] | None:
    """Returns (ahead, behind) of ref_b relative to ref_a. ahead = commits in B not in A."""
    result = run(
        ["git", "rev-list", "--left-right", "--count", f"{ref_a}...{ref_b}"],
        cwd=cwd,
        check=False,
    )
    if result.returncode != 0:
        return None
    left, right = result.stdout.strip().split()
    return int(right), int(left)  # (ahead, behind)


def worktree_add(
    repo_dir: Path, branch: str, worktree_dir: Path
) -> subprocess.CompletedProcess[str]:
    return run(
        ["git", "worktree", "add", "-b", branch, str(worktree_dir)],
        cwd=repo_dir,
        check=False,
    )


def worktree_remove(
    repo_dir: Path, worktree_dir: Path
) -> subprocess.CompletedProcess[str]:
    return run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=repo_dir,
        check=False,
    )


def branch_delete(
    repo_dir: Path, branch: str
) -> subprocess.CompletedProcess[str]:
    return run(["git", "branch", "-D", branch], cwd=repo_dir, check=False)


def pull(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    return run(["git", "pull", "--ff-only"], cwd=repo_dir, check=False)


def fetch(path: Path) -> subprocess.CompletedProcess[str]:
    return run(["git", "fetch"], cwd=path, check=False)


def clone(url: str) -> int:
    return subprocess.run(["git", "clone", url], cwd=REPO_DIR).returncode


def rebase(path: Path, onto: str, interactive: bool) -> int:
    cmd = ["git", "rebase"]
    if interactive:
        cmd.append("-i")
    cmd.append(onto)
    return subprocess.run(cmd, cwd=path).returncode


def push(path: Path, branch: str, force: bool) -> int:
    cmd = ["git", "push", "-u", "origin", branch]
    if force:
        cmd.append("--force-with-lease")
    return subprocess.run(cmd, cwd=path).returncode


def extra_git_dirs(project_dir: Path, known_repos: list[str]) -> list[Path]:
    known = set(known_repos)
    extras: list[Path] = []
    if not project_dir.is_dir():
        return extras
    for entry in sorted(project_dir.iterdir()):
        if entry.name in known or not entry.is_dir():
            continue
        if is_git_repo(entry):
            extras.append(entry)
    return extras
