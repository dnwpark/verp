from pathlib import Path

from rich.console import Console

from verp.git import (
    REPO_DIR,
    ahead_behind,
    current_branch,
    primary_branch,
    worktree_changes,
)

console = Console()


def _branch_vs_primary_lines(wt: Path, primary: str) -> list[str]:
    lines = []
    sync = ahead_behind(f"origin/{primary}", "HEAD", wt)
    if sync is not None:
        ahead, behind = sync
        if ahead:
            lines.append(
                f"[grey70]{ahead} commit{'s' if ahead != 1 else ''} ahead of {primary}[/grey70]"
            )
        if behind:
            lines.append(
                f"[grey70]{behind} commit{'s' if behind != 1 else ''} behind {primary}[/grey70]"
            )
    return lines


def _uncommitted_lines(wt: Path) -> list[str]:
    changed, untracked = worktree_changes(wt)
    lines = []
    if changed:
        lines.append(f"[dark_orange]{changed} modified[/dark_orange]")
    if untracked:
        lines.append(f"[dark_orange]{untracked} untracked[/dark_orange]")
    return lines


def _primary_vs_origin_lines(rp: Path, primary: str) -> list[str]:
    lines = []
    sync = ahead_behind(f"origin/{primary}", primary, rp)
    if sync is not None:
        ahead, behind = sync
        if ahead and behind:
            lines.append(f"[red]{primary} is out of sync with origin[/red]")
        elif behind:
            lines.append(f"[grey70]{primary} out of date, needs pull[/grey70]")
        elif ahead:
            lines.append(f"[grey70]{primary} out of date, needs push[/grey70]")
    return lines


def _branch_vs_origin_lines(wt: Path, branch: str) -> list[str]:
    sync = ahead_behind(f"origin/{branch}", "HEAD", wt)
    if sync is None:
        return ["[grey70]branch not pushed to origin[/grey70]"]
    ahead, behind = sync
    if ahead and behind:
        return ["[red]branch is out of sync with origin[/red]"]
    if ahead:
        return ["[grey70]branch out of date, needs push[/grey70]"]
    if behind:
        return ["[grey70]branch out of date, needs pull[/grey70]"]
    return []


def _print_status_lines(
    local_lines: list[str], remote_lines: list[str], indent: str
) -> None:
    if not local_lines and not remote_lines:
        console.print(f"{indent}  [green]up to date[/green]")
        return
    for line in local_lines:
        console.print(f"{indent}  {line}")
    if remote_lines:
        if local_lines:
            print()
        for line in remote_lines:
            console.print(f"{indent}  {line}")


def print_untracked_repo_status(path: Path, indent: str = "  ") -> None:
    print(f"{indent}{path.name}")
    branch = current_branch(path)
    if branch is None:
        console.print(f"{indent}  [red]could not determine branch[/red]")
        return
    local_lines = _uncommitted_lines(path)
    remote_lines = _branch_vs_origin_lines(path, branch)
    _print_status_lines(local_lines, remote_lines, indent)


def print_repo_status(
    repo: str, project_dir: Path, branch: str, indent: str = "  "
) -> None:
    wt = project_dir / repo
    rp = REPO_DIR / repo
    print(f"{indent}{repo}")

    if not wt.is_dir():
        console.print(f"{indent}  [red]worktree missing[/red]")
        return

    primary = primary_branch(rp)
    if not primary:
        console.print(f"{indent}  [red]primary branch unknown[/red]")
        return

    local_lines = _branch_vs_primary_lines(wt, primary) + _uncommitted_lines(wt)
    remote_lines = _primary_vs_origin_lines(
        rp, primary
    ) + _branch_vs_origin_lines(wt, branch)
    _print_status_lines(local_lines, remote_lines, indent)
