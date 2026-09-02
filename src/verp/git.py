import re
import subprocess
from pathlib import Path

from verp.paths import DATA_DIR

REPO_DIR = DATA_DIR / "repos"


def run(
    cmd: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, check=check, capture_output=True, text=True
    )


def branch_prefix() -> str:
    result = run(["git", "config", "verp.prefix"], check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


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
    remote_ref = f"origin/{branch}"
    remote_exists = run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=repo_dir,
        check=False,
    )
    cmd = ["git", "worktree", "add", "-b", branch, str(worktree_dir)]
    if remote_exists.returncode == 0 and remote_exists.stdout.strip():
        cmd.append(remote_ref)
    return run(cmd, cwd=repo_dir, check=False)


def worktree_remove(
    repo_dir: Path, worktree_dir: Path
) -> subprocess.CompletedProcess[str]:
    return run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=repo_dir,
        check=False,
    )


def worktree_prune(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    return run(["git", "worktree", "prune"], cwd=repo_dir, check=False)


def branch_exists(repo_dir: Path, branch: str) -> bool:
    return (
        run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_dir,
            check=False,
        ).returncode
        == 0
    )


def branch_delete(
    repo_dir: Path, branch: str
) -> subprocess.CompletedProcess[str]:
    return run(["git", "branch", "-D", branch], cwd=repo_dir, check=False)


_REF_PATTERNS = [
    # 'refs/foo' exists; cannot create 'refs/bar'
    re.compile(r"error: '(refs/[^']+)' exists; cannot create"),
    # cannot lock ref 'refs/foo': is at <sha> but expected <sha>
    re.compile(r"error: cannot lock ref '(refs/[^']+)'"),
]


def _resolve_ref_conflicts(stderr: str, path: Path) -> None:
    """Prune dead remote refs and forcibly delete any local refs that block incoming ones."""
    run(["git", "remote", "prune", "origin"], cwd=path, check=False)
    for pattern in _REF_PATTERNS:
        for m in pattern.finditer(stderr):
            run(["git", "update-ref", "-d", m.group(1)], cwd=path, check=False)


def _prune_and_retry(
    cmd: list[str], path: Path
) -> subprocess.CompletedProcess[str]:
    result = run(cmd, cwd=path, check=False)
    combined = result.stderr + result.stdout
    if (
        any(p.search(combined) for p in _REF_PATTERNS)
        or "remote prune origin" in combined
    ):
        _resolve_ref_conflicts(result.stderr, path)
        result = run(cmd, cwd=path, check=False)
    return result


def pull(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    return _prune_and_retry(["git", "pull", "--ff-only"], repo_dir)


def fetch(path: Path) -> subprocess.CompletedProcess[str]:
    return _prune_and_retry(["git", "fetch"], path)


# clone/rebase/push use subprocess.run() directly (not run()) so that git's
# live output — progress bars, editor invocations, push summaries — flows
# through to the user's terminal instead of being captured.


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
