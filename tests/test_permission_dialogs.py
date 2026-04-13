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
    SIZES,
    Terminal,
    TerminalSize,
    match_with_wildcards,
    normalize_screen,
    run_scenario,
)

_SIZE_PARAMS = [(s.cols, s.rows) for s in SIZES]
_SIZE_IDS = [f"{s.cols}x{s.rows}" for s in SIZES]


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
    # Check only the visible portion (last rows lines) — tmux scrollback may
    # retain Claude's old dialog content even after verp clears the screen.
    visible_dialog = "\n".join(norm_dialog.splitlines()[-result.size.rows :])
    assert (
        "Esc to cancel ·" not in visible_dialog
    ), "showing Claude dialog, not verp"

    # The user's prompt line must be visible in the scrollback.
    first_prompt_line = result.scenario.prompt.split("\n")[0]
    prompt_prefix = "❯ " + first_prompt_line.split("{tmp}")[0].rstrip()
    assert (
        prompt_prefix in norm_dialog
    ), f"Prompt prefix {prompt_prefix!r} not visible in screen_dialog"

    # Combined check: Claude's native preview (if any) + verp's dialog footer.
    # {tmp} is normalised, {any} is a wildcard.
    assert match_with_wildcards(
        norm_dialog, result.scenario.expected_screen_dialog
    ), (
        f"Screen dialog not matched.\n"
        f"Expected (with wildcards):\n{result.scenario.expected_screen_dialog!r}\n"
        f"Screen (last 30 lines):\n" + "\n".join(norm_dialog.splitlines()[-30:])
    )

    # ── screen_after ──────────────────────────────────────────────────────────

    # Dialog must be dismissed.
    assert (
        " Esc to cancel\n" not in norm_after
    ), "dialog still visible after response"

    # Prompt echo → (variable tool output) → result line.
    assert match_with_wildcards(
        norm_after, result.scenario.expected_screen_after
    ), (
        f"Screen after not matched.\n"
        f"Expected (with wildcards):\n{result.scenario.expected_screen_after!r}\n"
        f"Screen (last 30 lines):\n" + "\n".join(norm_after.splitlines()[-30:])
    )


def _run_scenario_test(
    scenario_name: str,
    tool: str,
    decision: str,
    cols: int,
    rows: int,
) -> None:
    result = run_scenario(
        SCENARIOS[scenario_name],
        terminal=Terminal.TMUX,
        size=TerminalSize(cols=cols, rows=rows),
        wrapper="verp",
    )
    assert result.success, result.error
    assert result.snapshot is not None
    assert result.snapshot.tool == tool
    assert result.snapshot.decision == decision
    assert result.snapshot.terminal_cols == cols
    assert result.snapshot.terminal_rows == rows
    assert result.snapshot.cursor_before is not None
    assert result.snapshot.cursor_after is not None
    assert result.snapshot.cursor_after.row == result.snapshot.cursor_before.row
    assert result.snapshot.cursor_after.col == result.snapshot.cursor_before.col
    _assert_screen(result)


pytestmark = [
    pytest.mark.skipif(
        shutil.which("claude") is None, reason="claude CLI not found"
    ),
    pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not found"),
]


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_write_allow(cols: int, rows: int) -> None:
    _run_scenario_test("write_allow", "Write", "allow", cols, rows)


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_write_deny(cols: int, rows: int) -> None:
    _run_scenario_test("write_deny", "Write", "deny", cols, rows)


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_bash_single_allow(cols: int, rows: int) -> None:
    _run_scenario_test("bash_single_allow", "Bash", "allow", cols, rows)


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_bash_single_deny(cols: int, rows: int) -> None:
    _run_scenario_test("bash_single_deny", "Bash", "deny", cols, rows)


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_bash_multiline(cols: int, rows: int) -> None:
    _run_scenario_test("bash_multiline", "Bash", "allow", cols, rows)


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_edit_allow(cols: int, rows: int) -> None:
    _run_scenario_test("edit_allow", "Edit", "allow", cols, rows)


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_edit_deny(cols: int, rows: int) -> None:
    _run_scenario_test("edit_deny", "Edit", "deny", cols, rows)


@pytest.mark.parametrize("cols,rows", _SIZE_PARAMS, ids=_SIZE_IDS)
def test_edit_replace_all(cols: int, rows: int) -> None:
    _run_scenario_test("edit_replace_all", "Edit", "allow", cols, rows)
