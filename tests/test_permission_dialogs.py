"""Integration tests for verp permission dialogs.

Requires: tmux, claude CLI, ANTHROPIC_API_KEY.
Uses tmux as the terminal backend so tests work headlessly in CI.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Generator

import pytest

from tests.dialog_runner import SCENARIOS, Terminal, TerminalSize, run_scenario


@pytest.fixture(autouse=True)
def _rate_limit_delay() -> Generator[None, None, None]:
    """Sleep between tests to avoid Claude API rate limiting."""
    yield
    time.sleep(45)


def _assert_screen(result: object) -> None:
    """Screen content assertions.

    screen_dialog and screen_after are the tmux scrollback captured while
    verp's dialog is visible and after it's dismissed respectively.
    pty_buffer is the raw PTY output before verp intercepted.

    Currently asserting non-empty as a baseline; update with more specific
    content assertions once the dialog alignment bug (col offset) is fixed.
    """
    from tests.dialog_runner import RunResult

    assert isinstance(result, RunResult)
    assert result.screen_dialog != ""  # should contain verp's dialog
    assert result.screen_after != ""  # should contain post-dialog output


pytestmark = [
    pytest.mark.skipif(
        shutil.which("claude") is None, reason="claude CLI not found"
    ),
    pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not found"),
]


# ── Allow scenarios ───────────────────────────────────────────────────────────


def test_write_allow() -> None:
    result = run_scenario(
        SCENARIOS["write_allow"],
        terminal=Terminal.TMUX,
        size=TerminalSize(cols=120, rows=30),
        wrapper="verp",
    )
    assert result.success, result.error
    assert result.snapshot is not None
    assert result.snapshot.tool == "Write"
    assert result.snapshot.decision == "allow"
    assert result.snapshot.cursor_before is not None
    assert result.snapshot.cursor_after is not None
    assert result.snapshot.cursor_after.row == result.snapshot.cursor_before.row
    assert result.snapshot.cursor_after.col == result.snapshot.cursor_before.col
    _assert_screen(result)


def test_bash_single_allow() -> None:
    result = run_scenario(
        SCENARIOS["bash_single_allow"],
        terminal=Terminal.TMUX,
        size=TerminalSize(cols=120, rows=30),
        wrapper="verp",
    )
    assert result.success, result.error
    assert result.snapshot is not None
    assert result.snapshot.tool == "Bash"
    assert result.snapshot.decision == "allow"
    assert result.snapshot.cursor_before is not None
    assert result.snapshot.cursor_after is not None
    assert result.snapshot.cursor_after.row == result.snapshot.cursor_before.row
    assert result.snapshot.cursor_after.col == result.snapshot.cursor_before.col
    _assert_screen(result)


# ── Deny scenarios ────────────────────────────────────────────────────────────


def test_write_deny() -> None:
    result = run_scenario(
        SCENARIOS["write_deny"],
        terminal=Terminal.TMUX,
        size=TerminalSize(cols=120, rows=30),
        wrapper="verp",
    )
    assert result.success, result.error
    assert result.snapshot is not None
    assert result.snapshot.tool == "Write"
    assert result.snapshot.decision == "deny"
    assert result.snapshot.cursor_before is not None
    assert result.snapshot.cursor_after is not None
    assert result.snapshot.cursor_after.row == result.snapshot.cursor_before.row
    assert result.snapshot.cursor_after.col == result.snapshot.cursor_before.col
    _assert_screen(result)


def test_bash_single_deny() -> None:
    result = run_scenario(
        SCENARIOS["bash_single_deny"],
        terminal=Terminal.TMUX,
        size=TerminalSize(cols=120, rows=30),
        wrapper="verp",
    )
    assert result.success, result.error
    assert result.snapshot is not None
    assert result.snapshot.tool == "Bash"
    assert result.snapshot.decision == "deny"
    assert result.snapshot.cursor_before is not None
    assert result.snapshot.cursor_after is not None
    assert result.snapshot.cursor_after.row == result.snapshot.cursor_before.row
    assert result.snapshot.cursor_after.col == result.snapshot.cursor_before.col
    _assert_screen(result)


# ── Terminal size variants ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cols,rows",
    [
        (80, 24),
        (120, 30),
        (200, 50),
        (60, 20),
    ],
    ids=["80x24", "120x30", "200x50", "60x20"],
)
def test_write_allow_sizes(cols: int, rows: int) -> None:
    result = run_scenario(
        SCENARIOS["write_allow"],
        terminal=Terminal.TMUX,
        size=TerminalSize(cols=cols, rows=rows),
        wrapper="verp",
    )
    assert result.success, result.error
    assert result.snapshot is not None
    assert result.snapshot.tool == "Write"
    assert result.snapshot.decision == "allow"
    assert result.snapshot.terminal_cols == cols
    assert result.snapshot.terminal_rows == rows
    assert result.snapshot.cursor_before is not None
    assert result.snapshot.cursor_after is not None
    assert result.snapshot.cursor_after.row == result.snapshot.cursor_before.row
    assert result.snapshot.cursor_after.col == result.snapshot.cursor_before.col
    _assert_screen(result)
