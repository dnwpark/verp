import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from verp.paths import DATA_DIR

_claude_version: str = "unknown"


def set_claude_version() -> None:
    global _claude_version
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            _claude_version = result.stdout.strip()
    except Exception:
        pass


def _verp_version() -> str:
    try:
        from importlib.metadata import version

        return version("verp")
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class PermissionSnapshot:
    timestamp: str
    verp_version: str
    claude_version: str
    terminal_cols: int
    terminal_rows: int
    cursor_before: tuple[int, int] | None  # before handle_permission_request
    cursor_start: tuple[int, int] | None  # after erasing Claude's dialog
    cursor_end: tuple[int, int] | None  # after erasing verp's dialog
    cursor_after: tuple[int, int] | None  # after handle_permission_request
    pty_buffer: str  # last bytes of PTY output before dialog, lossy-decoded
    tool: str
    directory: str
    decision: str


def build_snapshot(
    *,
    cursor_before: tuple[int, int] | None,
    cursor_start: tuple[int, int] | None,
    cursor_end: tuple[int, int] | None,
    cursor_after: tuple[int, int] | None,
    pty_buffer: bytes,
    tool: str,
    directory: str,
    decision: str,
) -> PermissionSnapshot:
    size = shutil.get_terminal_size((80, 24))
    return PermissionSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        verp_version=_verp_version(),
        claude_version=_claude_version,
        terminal_cols=size.columns,
        terminal_rows=size.lines,
        cursor_before=cursor_before,
        cursor_start=cursor_start,
        cursor_end=cursor_end,
        cursor_after=cursor_after,
        pty_buffer=pty_buffer.decode("utf-8", errors="replace"),
        tool=tool,
        directory=directory,
        decision=decision,
    )


def save_snapshot(snapshot: PermissionSnapshot) -> None:
    debug_dir = DATA_DIR / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    safe_ts = snapshot.timestamp.replace(":", "-")
    path = debug_dir / f"permission-{safe_ts}.json"
    path.write_text(json.dumps(asdict(snapshot), indent=2))
