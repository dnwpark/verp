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

    # Block to match in screen_dialog after normalization.
    # Use {tmp} for the temp dir, {any} as a multi-line wildcard for variable
    # Claude preview content, and    2. {any} for the variable option-2 line.
    expected_screen_dialog: str = ""
    # Block to match in screen_after after normalization.
    # Use {tmp} for the temp dir and {any} as a multi-line wildcard.
    expected_screen_after: str = ""
    # Marker to poll for in the pane before capturing screen_after.
    # Avoids capturing while Claude is still mid-operation.
    # Empty string falls back to a fixed time.sleep.
    result_poll_marker: str = ""
    # Files to create in {tmp} before launching Claude.
    # Each entry is (relative_path, content).
    setup_files: tuple[tuple[str, str], ...] = ()
    # Substring that must appear in the dialog screen for it to be the target.
    # Any dialogs that appear before the target (e.g. a Read before an Edit)
    # are automatically approved with 'y'.
    target_dialog_marker: str = ""


# Edit scenario: greet.py is pre-created in {tmp} so Claude can read then edit it.
_GREET_PY = 'def greet():\n    name = "world"\n    print(f"hello, {name}")\n    return name\n'

# ── Expected screen_dialog blocks ────────────────────────────────────────────
# Each block is checked against the normalised screen_dialog via
# match_with_wildcards.  {tmp} for normalised temp-dir paths, {any} for
# variable content.  Option 2 uses "   2. {any}" since its label varies.
#
# For Write/Edit: Claude's native preview (content/diff) sits above verp's
# question, bookended by ╌╌╌ separator lines.  {...} marks each separator
# (alias for {any} — the bottom one may be erased when verp needs extra
# scroll room).  {any} covers variable parts (path, context lines).
# For Bash: verp erases the full native dialog, so no preview is checked.

_WRITE_SCREEN_DIALOG = """\
 Create file
{any}hello.txt
{...}
  1 hello
{...}

 Write hello.txt?

 ❯ 1. Yes
   2. {any}
   3. No
 Esc to cancel"""

_BASH_SINGLE_SCREEN_DIALOG = """\
 Run: echo hello > {tmp}/hello.txt

 ❯ 1. Yes
   2. {any}
   3. No
 Esc to cancel"""

_BASH_MULTILINE_SCREEN_DIALOG = """\
 Run: bash -c "echo hello > {tmp}/hello.txt
echo world > {tmp}/world.txt"

 ❯ 1. Yes
   2. {any}
   3. No
 Esc to cancel"""

_EDIT_SCREEN_DIALOG = """\
 Edit file
{any}greet.py
{any}
{...}
 1  def greet():
 2 -    name = "world"
 2 +    name = "verp"
 3      print(f"hello, {name}")
 4      return name
{...}

 Edit greet.py?

 ❯ 1. Yes
   2. {any}
   3. No
 Esc to cancel"""

_EDIT_REPLACE_ALL_SCREEN_DIALOG = """\
 Edit file
{any}greet.py
{any}
{...}
 1  def greet():
 2 -    name = "world"
 3 -    print(f"hello, {name}")
 4 -    return name
 2 +    NAME = "world"
 3 +    print(f"hello, {NAME}")
 4 +    return NAME
{...}

 Edit greet.py? (replace all)

 ❯ 1. Yes
   2. {any}
   3. No
 Esc to cancel"""

# ── Expected screen_after blocks ─────────────────────────────────────────────
# Anchor on the prompt prefix (non-path, wrapping-safe), then {any} for Claude's
# tool output, then the result line with its "  ⎿ \xa0" prefix.
# Bash deny uses "Interrupted" (verp's Ctrl+C); Write/Edit deny uses
# "User rejected" (Claude's tool denial).

_WRITE_ALLOW_AFTER = """\
❯ write 'hello' to{any}
  ⎿ \xa0Allowed by PermissionRequest hook"""

_WRITE_DENY_AFTER = """\
❯ write 'hello' to{any}
  ⎿ \xa0User rejected"""

_BASH_SINGLE_ALLOW_AFTER = """\
❯ echo hello >{any}
  ⎿ \xa0Allowed by PermissionRequest hook"""

_BASH_SINGLE_DENY_AFTER = """\
❯ echo hello >{any}
     Interrupted"""

_BASH_MULTILINE_ALLOW_AFTER = """\
❯ run in a single step{any}
  ⎿ \xa0Allowed by PermissionRequest hook"""

_EDIT_ALLOW_AFTER = """\
❯ edit {tmp}/greet.py{any}
  ⎿ \xa0Allowed by PermissionRequest hook"""

_EDIT_DENY_AFTER = """\
❯ edit {tmp}/greet.py{any}
  ⎿ \xa0User rejected"""

_EDIT_REPLACE_ALL_AFTER = """\
❯ in {tmp}/greet.py{any}
  ⎿ \xa0Allowed by PermissionRequest hook"""

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario(
            "write_allow",
            "write 'hello' to {tmp}/hello.txt",
            "Write",
            True,
            expected_screen_dialog=_WRITE_SCREEN_DIALOG,
            expected_screen_after=_WRITE_ALLOW_AFTER,
            result_poll_marker="Allowed by PermissionRequest hook",
        ),
        Scenario(
            "write_deny",
            "write 'hello' to {tmp}/hello.txt",
            "Write",
            False,
            expected_screen_dialog=_WRITE_SCREEN_DIALOG,
            expected_screen_after=_WRITE_DENY_AFTER,
        ),
        Scenario(
            "bash_single_allow",
            "echo hello > {tmp}/hello.txt",
            "Bash",
            True,
            expected_screen_dialog=_BASH_SINGLE_SCREEN_DIALOG,
            expected_screen_after=_BASH_SINGLE_ALLOW_AFTER,
            result_poll_marker="Allowed by PermissionRequest hook",
        ),
        Scenario(
            "bash_single_deny",
            "echo hello > {tmp}/hello.txt",
            "Bash",
            False,
            expected_screen_dialog=_BASH_SINGLE_SCREEN_DIALOG,
            expected_screen_after=_BASH_SINGLE_DENY_AFTER,
        ),
        Scenario(
            "bash_multiline",
            "run in a single step, commands with newlines, no && or ;\n"
            'bash -c "echo hello > {tmp}/hello.txt\n'
            'echo world > {tmp}/world.txt"',
            "Bash",
            True,
            expected_screen_dialog=_BASH_MULTILINE_SCREEN_DIALOG,
            expected_screen_after=_BASH_MULTILINE_ALLOW_AFTER,
            result_poll_marker="Allowed by PermissionRequest hook",
        ),
        Scenario(
            "edit_allow",
            'edit {tmp}/greet.py to change "world" to "verp"',
            "Edit",
            True,
            setup_files=(("greet.py", _GREET_PY),),
            target_dialog_marker="Edit greet.py?",
            expected_screen_dialog=_EDIT_SCREEN_DIALOG,
            expected_screen_after=_EDIT_ALLOW_AFTER,
            result_poll_marker="Allowed by PermissionRequest hook",
        ),
        Scenario(
            "edit_deny",
            'edit {tmp}/greet.py to change "world" to "verp"',
            "Edit",
            False,
            setup_files=(("greet.py", _GREET_PY),),
            target_dialog_marker="Edit greet.py?",
            expected_screen_dialog=_EDIT_SCREEN_DIALOG,
            expected_screen_after=_EDIT_DENY_AFTER,
        ),
        Scenario(
            "edit_replace_all",
            'in {tmp}/greet.py, replace ALL occurrences of the variable name "name"'
            ' with "NAME" using replace_all=true',
            "Edit",
            True,
            setup_files=(("greet.py", _GREET_PY),),
            target_dialog_marker="Edit greet.py? (replace all)",
            expected_screen_dialog=_EDIT_REPLACE_ALL_SCREEN_DIALOG,
            expected_screen_after=_EDIT_REPLACE_ALL_AFTER,
            result_poll_marker="Allowed by PermissionRequest hook",
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
    tmp_dir: str  # temp dir path for normalizing paths in assertions
    success: bool
    error: str | None


def normalize_screen(screen: str, tmp_dir: str) -> str:
    """Replace the test temp dir with {tmp} and strip tmux line-padding.

    tmux capture-pane -p pads every line with spaces to the terminal width.
    Stripping trailing whitespace per line makes comparisons terminal-width-agnostic.
    Leading whitespace is preserved — it is meaningful in verp's dialog rendering.
    """
    replaced = screen.replace(tmp_dir, "{tmp}")
    return "\n".join(line.rstrip() for line in replaced.split("\n"))


def match_with_wildcards(screen: str, expected: str) -> bool:
    """Return True if *expected* matches *screen*.

    The expected string is compiled into a regex:
    - ``{any}``  — matches any content (``[\\s\\S]*?``, non-greedy)
    - ``{...}``    — matches a full line of ``╌`` characters (``╌+``)
    - everything else is matched literally (including ``{tmp}``)
    """
    import re

    parts = re.split(r"(\{any\}|\{\.\.\.})", expected)
    pattern = ""
    for part in parts:
        if part == "{any}":
            pattern += r"[\s\S]*?"
        elif part == "{...}":
            pattern += r"╌+"
        else:
            pattern += re.escape(part)
    return bool(re.search(pattern, screen, re.DOTALL))


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


def _poll_for_dialog_gone(
    session: str, tool: str, wrapper: Wrapper, timeout: float = 15
) -> None:
    """Wait until the permission dialog is no longer visible (best-effort)."""
    marker = _VERP_DIALOG_MARKER if wrapper == "verp" else _NATIVE_DIALOG_MARKER
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

_NATIVE_DIALOG_MARKER = "Esc to cancel ·"


def _poll_for_dialog(
    session: str, tool: str, wrapper: Wrapper, timeout: float = 60
) -> bool:
    """Poll until the permission dialog (verp or native) is visible."""
    marker = _VERP_DIALOG_MARKER if wrapper == "verp" else _NATIVE_DIALOG_MARKER
    return _poll_tmux(session, marker, timeout)


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
        # Create any files the scenario needs in the temp dir before Claude starts.
        for rel_path, content in scenario.setup_files:
            target = Path(test_dir) / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

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
                tmp_dir=test_dir,
                success=False,
                error="Claude did not become ready within timeout",
            )
        # Type the prompt and submit
        subprocess.run(
            ["tmux", "send-keys", "-t", session, prompt, "Enter"],
            check=True,
        )
        # Poll for dialogs, auto-approving any that aren't the target.
        for _attempt in range(10):
            if not _poll_for_dialog(session, scenario.tool, wrapper):
                screen = _tmux_capture(session)
                return RunResult(
                    scenario=scenario,
                    size=size,
                    wrapper=wrapper,
                    terminal=Terminal.TMUX,
                    snapshot=None,
                    screen_dialog="",
                    screen_after=screen,
                    tmp_dir=test_dir,
                    success=False,
                    error="Dialog did not appear within timeout",
                )
            if not scenario.target_dialog_marker:
                break  # no filtering needed — first dialog is the target
            screen = _tmux_capture(session)
            norm = normalize_screen(screen, test_dir)
            if scenario.target_dialog_marker in norm:
                break  # found the target dialog
            # Pre-approve this dialog and wait for it to dismiss
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "-l", "y"], check=True
            )
            _poll_for_dialog_gone(session, scenario.tool, wrapper)
        else:
            screen = _tmux_capture(session)
            return RunResult(
                scenario=scenario,
                size=size,
                wrapper=wrapper,
                terminal=Terminal.TMUX,
                snapshot=None,
                screen_dialog="",
                screen_after=screen,
                tmp_dir=test_dir,
                success=False,
                error="Target dialog did not appear after pre-approvals",
            )

        screen_dialog = _tmux_capture(session)

        # Send the keypress to approve or deny (dialog handles y/n, not digits)
        key = "y" if scenario.approve else "n"
        subprocess.run(
            ["tmux", "send-keys", "-t", session, "-l", key], check=True
        )

        # Wait for dialog to dismiss, then wait for the result to appear before
        # capturing — avoids snapshotting while Claude is still mid-operation.
        _poll_for_dialog_gone(session, scenario.tool, wrapper)
        if scenario.result_poll_marker:
            _poll_tmux(session, scenario.result_poll_marker, timeout=30)
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
            tmp_dir=test_dir,
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
            tmp_dir=test_dir,
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
        tmp_dir=test_dir,
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
        tmp_dir=test_dir,
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
        tmp_dir=test_dir,
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
