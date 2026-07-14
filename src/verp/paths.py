import os
from pathlib import Path

DATA_DIR = (
    Path(os.environ["VERP_DATA_DIR"])
    if "VERP_DATA_DIR" in os.environ
    else Path.home() / ".local" / "share" / "verp"
)
CLAUDE_DIR = DATA_DIR / "claude_dir"
PI_DIR = DATA_DIR / "pi_dir"

CONFIG_DIR = Path.home() / ".config" / "verp"
USER_CLAUDE_DIR = CONFIG_DIR / ".claude"


def verp_sock_path(pid: int) -> str:
    return f"/tmp/verp-{pid}.sock"


def verp_sock_pid(sock_path: str) -> int | None:
    try:
        return int(Path(sock_path).stem.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        return None
