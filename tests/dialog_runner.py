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

    # Exact multi-line dialog block to match in screen_dialog after normalization.
    # Use {tmp} for the temp dir and {option2} for the variable option-2 line.
    # Empty string disables block assertion (e.g. for multiline-command scenarios
    # where the command display is non-deterministic).
    expected_dialog_block: str = ""
    # Block to match in screen_after after normalization.
    # Use {tmp} for the temp dir and {...} as a multi-line wildcard for variable
    # content between the user's prompt echo and the result line.
    expected_after_block: str = ""
    # Marker to poll for in the pane before capturing screen_after.
    # Avoids capturing while Claude is still mid-operation.
    # Empty string falls back to a fixed time.sleep.
    result_poll_marker: str = ""


# Dialog block constants.
# The leading \n matches the blank line that opens verp's dialog; {option2} is
# a placeholder for the variable "always allow" option whose label depends on
# Claude's permission_suggestions.
_WRITE_DIALOG = """
 Write hello.txt?

 1 hello

 ❯ 1. Yes
{option2}
   3. No
 Esc to cancel"""

# Bash scenarios: use the minimal command directly so Claude passes it through
# unchanged, making the dialog question deterministic.
_BASH_SINGLE_DIALOG = """
 Run: echo hello > {tmp}/hello.txt

 ❯ 1. Yes
{option2}
   3. No
 Esc to cancel"""

_BASH_MULTILINE_DIALOG = """
 Run: bash -c "echo hello > {tmp}/hello.txt
echo world > {tmp}/world.txt"

 ❯ 1. Yes
{option2}
   3. No
 Esc to cancel"""

# After-screen block constants.
# Format: anchor (non-path prefix of the prompt, no trailing newline so it
# matches both wrapped and non-wrapped terminals){...}result line (with its
# leading \n + "  ⎿ \xa0" prefix that Claude renders for tool results).
#
# Bash deny triggers verp's interrupt (Ctrl+C), which Claude surfaces as
# "Interrupted"; Write deny returns behavior:deny and Claude shows
# "User rejected".
_WRITE_ALLOW_AFTER = """\
❯ write 'hello' to{...}
  ⎿ \xa0Allowed by PermissionRequest hook"""

_WRITE_DENY_AFTER = """\
❯ write 'hello' to{...}
  ⎿ \xa0User rejected"""

_BASH_SINGLE_ALLOW_AFTER = """\
❯ echo hello >{...}
  ⎿ \xa0Allowed by PermissionRequest hook"""

_BASH_SINGLE_DENY_AFTER = """\
❯ echo hello >{...}
     Interrupted"""

_BASH_MULTILINE_ALLOW_AFTER = """\
❯ run in a single step{...}
  ⎿ \xa0Allowed by PermissionRequest hook"""

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario(
            "write_allow",
            "write 'hello' to {tmp}/hello.txt",
            "Write",
            True,
            expected_dialog_block=_WRITE_DIALOG,
            expected_after_block=_WRITE_ALLOW_AFTER,
            result_poll_marker="Allowed by PermissionRequest hook",
        ),
        Scenario(
            "write_deny",
            "write 'hello' to {tmp}/hello.txt",
            "Write",
            False,
            expected_dialog_block=_WRITE_DIALOG,
            expected_after_block=_WRITE_DENY_AFTER,
        ),
        Scenario(
            "bash_single_allow",
            "echo hello > {tmp}/hello.txt",
            "Bash",
            True,
            expected_dialog_block=_BASH_SINGLE_DIALOG,
            expected_after_block=_BASH_SINGLE_ALLOW_AFTER,
            result_poll_marker="Allowed by PermissionRequest hook",
        ),
        Scenario(
            "bash_single_deny",
            "echo hello > {tmp}/hello.txt",
            "Bash",
            False,
            expected_dialog_block=_BASH_SINGLE_DIALOG,
            expected_after_block=_BASH_SINGLE_DENY_AFTER,
        ),
        Scenario(
            "bash_multiline",
            # Real newlines in the prompt: tmux sends each line as a separate
            # message, and Claude reassembles them into a single bash -c command
            # with an embedded newline — exercising verp's multiline dialog path.
            "run in a single step, commands with newlines, no && or ;\n"
            'bash -c "echo hello > {tmp}/hello.txt\n'
            'echo world > {tmp}/world.txt"',
            "Bash",
            True,
            expected_dialog_block=_BASH_MULTILINE_DIALOG,
            expected_after_block=_BASH_MULTILINE_ALLOW_AFTER,
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
    """Return True if every section of *expected* (split on ``{...}``) appears
    in *screen* in order.

    ``{...}`` acts as a multi-line wildcard: any amount of content (including
    none) may appear between consecutive sections.  All other text in *expected*
    is matched literally, so ``{tmp}`` is treated as the normalised placeholder
    string, not a wildcard.
    """
    pos = 0
    for section in expected.split("{...}"):
        if not section:
            continue
        idx = screen.find(section, pos)
        if idx == -1:
            return False
        pos = idx + len(section)
    return True


def extract_dialog_block(screen: str) -> str | None:
    """Extract the verp dialog block from a tmux-captured screen.

    Strategy:
    1. Anchor on the trailing " Esc to cancel" line.
    2. Scan back to find " ❯ 1. Yes" (the selected option); the blank
       immediately before it separates dialog content from the options.
    3. Continue scanning back through question/content lines to find the
       opening blank.  Internal blanks (e.g. between the question and
       file-content lines) are distinguished from the opening blank by
       checking the line above: if it starts with " " it is still dialog
       content; if it does not (or we are at the screen edge) it is the
       opening blank that precedes the entire dialog.
    """
    lines = screen.split("\n")
    # 1. Find the last " Esc to cancel" line.
    esc_idx = next(
        (
            i
            for i in range(len(lines) - 1, -1, -1)
            if lines[i].rstrip() == " Esc to cancel"
        ),
        None,
    )
    if esc_idx is None:
        return None
    # 2. Find " ❯ 1. Yes" scanning backwards from esc_idx.
    option1_idx = next(
        (i for i in range(esc_idx - 1, -1, -1) if lines[i].startswith(" ❯")),
        None,
    )
    if option1_idx is None or option1_idx == 0:
        return None
    if lines[option1_idx - 1].strip() != "":
        return None
    blank_before_opts = option1_idx - 1
    # 3. Scan backwards to find the opening blank.
    start_idx = None
    for i in range(blank_before_opts - 1, -1, -1):
        if lines[i].strip() == "":
            above = lines[i - 1] if i > 0 else ""
            if not above.startswith(" "):
                start_idx = i
                break
    if start_idx is None:
        return None
    return "\n".join(lines[start_idx : esc_idx + 1])


def normalize_dialog_option2(block: str) -> str:
    """Collapse option-2 line(s) — including terminal-wrapped continuations —
    into the single literal token ``{option2}``.

    Option 2 starts with ``   2.`` and its visual continuations are any lines
    that follow before option 3 (``   3.``), the selected-option marker
    (`` ❯``), or the footer (`` Esc to cancel``).
    """
    lines = block.split("\n")
    result: list[str] = []
    in_opt2 = False
    for line in lines:
        if line.startswith("   2."):
            if not in_opt2:
                result.append("{option2}")
                in_opt2 = True
        elif in_opt2 and not (
            line.startswith("   3.")
            or line.startswith(" ❯")
            or line.rstrip() == " Esc to cancel"
        ):
            # Wrapped continuation of option 2 — absorb into placeholder.
            pass
        else:
            in_opt2 = False
            result.append(line)
    return "\n".join(result)


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
