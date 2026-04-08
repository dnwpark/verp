"""Integration tests for verp permission dialogs.

Requires: tmux, claude CLI, ANTHROPIC_API_KEY.
Uses tmux as the terminal backend so tests work headlessly in CI.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Generator

import pytest

from tests.dialog_runner import (
    SCENARIOS,
    Terminal,
    TerminalSize,
    extract_dialog_block,
    match_with_wildcards,
    normalize_dialog_option2,
    normalize_screen,
    run_scenario,
)


@pytest.fixture(autouse=True)
def _rate_limit_delay() -> Generator[None, None, None]:
    """Sleep between tests to avoid Claude API rate limiting."""
    yield
    time.sleep(45)


def _assert_screen(result: object) -> None:
    """Screen content assertions for dialog visibility and dismissal."""
    from tests.dialog_runner import RunResult

    assert isinstance(result, RunResult)

    norm_dialog = normalize_screen(result.screen_dialog, result.tmp_dir)
    norm_after = normalize_screen(result.screen_after, result.tmp_dir)

    # ── screen_dialog ─────────────────────────────────────────────────────────

    # Must not be Claude's native dialog (which uses "·" in its footer).
    assert (
        "Esc to cancel ·" not in norm_dialog
    ), "showing Claude dialog, not verp"

    # The user's prompt line must be visible in the scrollback.
    # Claude's CLI prefixes the human turn with "❯ ".
    # Use the first line of the prompt and the non-path prefix only: multiline
    # prompts are sent as separate messages (each \n becomes an Enter press via
    # tmux), and at narrow widths a path may wrap across rows preventing {tmp}
    # normalisation from working on split content.
    first_prompt_line = result.scenario.prompt.split("\n")[0]
    prompt_prefix = "❯ " + first_prompt_line.split("{tmp}")[0].rstrip()
    assert (
        prompt_prefix in norm_dialog
    ), f"Prompt prefix {prompt_prefix!r} not visible in screen_dialog"

    # Exact dialog box comparison (includes blank lines and structure).
    # Option 2 is collapsed to {option2} because its label varies with
    # Claude's permission_suggestions.
    block = extract_dialog_block(norm_dialog)
    assert block is not None, "verp dialog block not found in screen_dialog"
    assert (
        normalize_dialog_option2(block) == result.scenario.expected_dialog_block
    ), (
        f"Dialog block mismatch.\n"
        f"Got (normalized):\n{normalize_dialog_option2(block)!r}\n"
        f"Expected:\n{result.scenario.expected_dialog_block!r}"
    )

    # ── screen_after ──────────────────────────────────────────────────────────

    # Dialog must be dismissed.
    assert (
        " Esc to cancel\n" not in norm_after
    ), "dialog still visible after response"

    # Check the after block: prompt echo → (variable tool output) → result line.
    assert match_with_wildcards(
        norm_after, result.scenario.expected_after_block
    ), (
        f"After block not matched.\n"
        f"Expected (with {{...}} wildcards):\n{result.scenario.expected_after_block!r}\n"
        f"Screen (last 30 lines):\n" + "\n".join(norm_after.splitlines()[-30:])
    )


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
