#!/usr/bin/env python3
"""Unified permission dialog test runner.

Spawns a Claude session (native or verp-wrapped) inside a terminal backend
(tmux, kitty, iTerm2, Apple Terminal), triggers a permission dialog, sends
an approval/denial keypress, and captures the results.

Usage:
    python tests/dialog_runner.py --scenario write_allow --size 120x30
    python tests/dialog_runner.py --terminal kitty --scenario all
    python tests/dialog_runner.py --wrapper native --scenario write_allow
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from verp.debug import CursorPosition, PermissionSnapshot

# ── Types ─────────────────────────────────────────────────────────────────────


class Terminal(StrEnum):
    TMUX = "tmux"
    KITTY = "kitty"
    ITERM2 = "iterm2"
    APPLE_TERMINAL = "terminal"


Wrapper = Literal["verp", "native"]


@dataclass(frozen=True, kw_only=True)
class TerminalSize:
    cols: int
    rows: int


# ── Scenarios ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scenario:
    name: str
    prompt: str  # may contain {tmp} placeholder
    tool: str
    approve: bool


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario(
            "write_allow", "write 'hello' to {tmp}/hello.txt", "Write", True
        ),
        Scenario(
            "write_deny", "write 'hello' to {tmp}/hello.txt", "Write", False
        ),
        Scenario(
            "bash_single_allow",
            "bash -c 'echo hello > {tmp}/hello.txt'",
            "Bash",
            True,
        ),
        Scenario(
            "bash_single_deny",
            "bash -c 'echo hello > {tmp}/hello.txt'",
            "Bash",
            False,
        ),
        Scenario(
            "bash_multiline",
            "bash -c 'echo hello\\necho world'",
            "Bash",
            True,
        ),
    ]
}

SIZES: list[TerminalSize] = [
    TerminalSize(cols=80, rows=24),
    TerminalSize(cols=120, rows=30),
    TerminalSize(cols=200, rows=50),
    TerminalSize(cols=60, rows=20),
]


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    scenario: Scenario
    size: TerminalSize
    wrapper: Wrapper
    terminal: Terminal
    snapshot: PermissionSnapshot | None
    screen_dialog: str  # scrollback captured while the verp dialog is visible
    screen_after: str
    success: bool
    error: str | None


# ── Terminal detection ────────────────────────────────────────────────────────


def detect_terminal() -> Terminal:
    if os.environ.get("KITTY_WINDOW_ID"):
        return Terminal.KITTY
    term = os.environ.get("TERM_PROGRAM", "")
    if term == "iTerm.app":
        return Terminal.ITERM2
    if term == "Apple_Terminal":
        return Terminal.APPLE_TERMINAL
    return Terminal.TMUX


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_launch_command(wrapper: Wrapper, data_dir: str) -> str:
    """Build the command to launch the Claude session (interactive, no prompt)."""
    env_prefix = f"VERP_DEBUG=1 VERP_DATA_DIR={data_dir}"
    if wrapper == "verp":
        return f"{env_prefix} verp claude"
    return "claude"


def _poll_tmux(session: str, marker: str, timeout: float = 30) -> bool:
    """Poll visible tmux pane content until marker appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True,
            text=True,
        )
        if marker in result.stdout:
            return True
        time.sleep(0.5)
    return False


def _poll_for_claude_ready(session: str, timeout: float = 60) -> bool:
    """Wait until Claude's interactive input prompt is visible."""
    # Claude's idle input box shows this shortcut hint
    return _poll_tmux(session, "? for shortcuts", timeout)


def _poll_for_dialog_gone(session: str, tool: str, timeout: float = 15) -> None:
    """Wait until verp's dialog is no longer visible (best-effort)."""
    marker = _VERP_DIALOG_MARKER
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True,
            text=True,
        )
        if marker not in result.stdout:
            return
        time.sleep(0.5)


_VERP_DIALOG_MARKER = " Esc to cancel\n"
# Verp always ends its dialog with " Esc to cancel\n" (space + text, no "·").
# Claude's native dialog ends with "Esc to cancel · Tab to amend", so this
# marker reliably identifies verp's dialog regardless of permission suggestions.


def _poll_for_dialog(session: str, tool: str, timeout: float = 60) -> bool:
    """Poll until verp's permission dialog is visible."""
    return _poll_tmux(session, _VERP_DIALOG_MARKER, timeout)


def _tmux_capture(session: str) -> str:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-S", "-1000"],
        capture_output=True,
        text=True,
    )
    return result.stdout


# ── Terminal backends ─────────────────────────────────────────────────────────


def run_tmux(
    scenario: Scenario,
    size: TerminalSize,
    wrapper: Wrapper,
    test_dir: str,
) -> RunResult:
    session = f"verp_test_{os.getpid()}_{int(time.monotonic() * 1000) % 100000}"
    data_dir = str(Path(test_dir) / "data")
    prompt = scenario.prompt.replace("{tmp}", test_dir)
    launch_cmd = _build_launch_command(wrapper, data_dir)

    try:
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session,
                "-x",
                str(size.cols),
                "-y",
                str(size.rows),
            ],
            check=True,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", session, launch_cmd, "Enter"],
            check=True,
        )
        # Wait for Claude's interactive input prompt to appear
        if not _poll_for_claude_ready(session):
            screen = _tmux_capture(session)
            return RunResult(
                scenario=scenario,
                size=size,
                wrapper=wrapper,
                terminal=Terminal.TMUX,
                snapshot=None,
                screen_dialog="",
                screen_after=screen,
                success=False,
                error="Claude did not become ready within timeout",
            )
        # Type the prompt and submit
        subprocess.run(
            ["tmux", "send-keys", "-t", session, prompt, "Enter"],
            check=True,
        )
        if not _poll_for_dialog(session, scenario.tool):
            screen = _tmux_capture(session)
            return RunResult(
                scenario=scenario,
                size=size,
                wrapper=wrapper,
                terminal=Terminal.TMUX,
                snapshot=None,
                screen_dialog="",
                screen_after=screen,
                success=False,
                error="Dialog did not appear within timeout",
            )

        screen_dialog = _tmux_capture(session)

        # Send the keypress to approve or deny (dialog handles y/n, not digits)
        key = "y" if scenario.approve else "n"
        subprocess.run(
            ["tmux", "send-keys", "-t", session, "-l", key], check=True
        )

        # Wait for dialog to dismiss then capture final screen
        _poll_for_dialog_gone(session, scenario.tool)
        time.sleep(1)
        screen = _tmux_capture(session)

        snapshot = None
        if wrapper == "verp":
            debug_dir = Path(data_dir) / "debug"
            files = sorted(debug_dir.glob("permission-*.json"))
            if files:
                raw = json.loads(files[-1].read_text())
                snapshot = PermissionSnapshot(
                    timestamp=raw["timestamp"],
                    verp_version=raw["verp_version"],
                    claude_version=raw["claude_version"],
                    terminal_cols=raw["terminal_cols"],
                    terminal_rows=raw["terminal_rows"],
                    cursor_before=(
                        CursorPosition(
                            row=raw["cursor_before"]["row"],
                            col=raw["cursor_before"]["col"],
                        )
                        if raw["cursor_before"]
                        else None
                    ),
                    cursor_start=(
                        CursorPosition(
                            row=raw["cursor_start"]["row"],
                            col=raw["cursor_start"]["col"],
                        )
                        if raw["cursor_start"]
                        else None
                    ),
                    cursor_end=(
                        CursorPosition(
                            row=raw["cursor_end"]["row"],
                            col=raw["cursor_end"]["col"],
                        )
                        if raw["cursor_end"]
                        else None
                    ),
                    cursor_after=(
                        CursorPosition(
                            row=raw["cursor_after"]["row"],
                            col=raw["cursor_after"]["col"],
                        )
                        if raw["cursor_after"]
                        else None
                    ),
                    pty_buffer=raw["pty_buffer"],
                    tool=raw["tool"],
                    directory=raw["directory"],
                    decision=raw["decision"],
                )

        return RunResult(
            scenario=scenario,
            size=size,
            wrapper=wrapper,
            terminal=Terminal.TMUX,
            snapshot=snapshot,
            screen_dialog=screen_dialog,
            screen_after=screen,
            success=True,
            error=None,
        )
    except Exception as e:
        return RunResult(
            scenario=scenario,
            size=size,
            wrapper=wrapper,
            terminal=Terminal.TMUX,
            snapshot=None,
            screen_dialog="",
            screen_after="",
            success=False,
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            capture_output=True,
        )


def run_kitty(
    scenario: Scenario,
    size: TerminalSize,
    wrapper: Wrapper,
    test_dir: str,
) -> RunResult:
    # TODO: implement kitty @ launch backend
    return RunResult(
        scenario=scenario,
        size=size,
        wrapper=wrapper,
        terminal=Terminal.KITTY,
        snapshot=None,
        screen_dialog="",
        screen_after="",
        success=False,
        error="kitty backend not yet implemented",
    )


def run_iterm2(
    scenario: Scenario,
    size: TerminalSize,
    wrapper: Wrapper,
    test_dir: str,
) -> RunResult:
    # TODO: implement iTerm2 osascript backend
    return RunResult(
        scenario=scenario,
        size=size,
        wrapper=wrapper,
        terminal=Terminal.ITERM2,
        snapshot=None,
        screen_dialog="",
        screen_after="",
        success=False,
        error="iTerm2 backend not yet implemented",
    )


def run_apple_terminal(
    scenario: Scenario,
    size: TerminalSize,
    wrapper: Wrapper,
    test_dir: str,
) -> RunResult:
    # TODO: implement Apple Terminal osascript backend
    return RunResult(
        scenario=scenario,
        size=size,
        wrapper=wrapper,
        terminal=Terminal.APPLE_TERMINAL,
        snapshot=None,
        screen_dialog="",
        screen_after="",
        success=False,
        error="Apple Terminal backend not yet implemented",
    )


_BACKENDS = {
    Terminal.TMUX: run_tmux,
    Terminal.KITTY: run_kitty,
    Terminal.ITERM2: run_iterm2,
    Terminal.APPLE_TERMINAL: run_apple_terminal,
}


# ── Entry point ───────────────────────────────────────────────────────────────


def run_scenario(
    scenario: Scenario,
    *,
    size: TerminalSize,
    wrapper: Wrapper,
    terminal: Terminal,
) -> RunResult:
    backend = _BACKENDS[terminal]

    with tempfile.TemporaryDirectory(prefix="verp_test_") as test_dir:
        data_dir = Path(test_dir) / "data"
        (data_dir / "debug").mkdir(parents=True)
        return backend(scenario, size, wrapper, test_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Permission dialog test runner"
    )
    parser.add_argument(
        "--scenario",
        default="all",
        help=f"Scenario name or 'all'. Available: {', '.join(SCENARIOS)}",
    )
    parser.add_argument(
        "--terminal",
        default="auto",
        choices=["auto"] + [t.value for t in Terminal],
    )
    parser.add_argument(
        "--wrapper",
        default="verp",
        choices=["verp", "native"],
    )
    parser.add_argument(
        "--size",
        default="120x30",
        help="Terminal size as COLSxROWS (e.g. 80x24)",
    )
    args = parser.parse_args()

    cols, rows = (int(x) for x in args.size.split("x"))
    size = TerminalSize(cols=cols, rows=rows)
    terminal = (
        detect_terminal()
        if args.terminal == "auto"
        else Terminal(args.terminal)
    )
    wrapper: Wrapper = args.wrapper

    scenarios = (
        list(SCENARIOS.values())
        if args.scenario == "all"
        else [SCENARIOS[args.scenario]]
    )

    for scenario in scenarios:
        result = run_scenario(
            scenario,
            size=size,
            wrapper=wrapper,
            terminal=terminal,
        )
        status = "✓" if result.success else "✗"
        print(
            f"{status} {result.scenario.name}"
            f" ({result.size.cols}x{result.size.rows}"
            f" {result.terminal} {result.wrapper})"
        )
        if result.error:
            print(f"  error: {result.error}")
        if result.snapshot:
            snap = result.snapshot
            print(f"  tool={snap.tool} decision={snap.decision}")
            print(
                f"  cursor_before={snap.cursor_before}"
                f" cursor_after={snap.cursor_after}"
            )
        if result.screen_after and not result.success:
            print("  screen_after (last 5 lines):")
            for line in result.screen_after.strip().split("\n")[-5:]:
                print(f"    {line}")


if __name__ == "__main__":
    main()
