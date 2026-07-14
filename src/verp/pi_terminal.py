import os
import subprocess

from verp.claude_terminal import get_project_system_prompt
from verp.db import remove_agents_by_pid
from verp.paths import DATA_DIR


def _build_pi_cmd(args: list[str]) -> list[str]:
    ext_path = DATA_DIR / "pi-extension.ts"
    system_prompt = get_project_system_prompt()
    append_system = (
        ["--append-system-prompt", system_prompt] if system_prompt else []
    )
    return ["pi", "--extension", str(ext_path)] + append_system + args


def cmd_pi(args: list[str]) -> int:
    cmd = _build_pi_cmd(args)
    env = os.environ.copy()
    env["VERP_PID"] = str(os.getpid())
    try:
        result = subprocess.run(cmd, env=env)
        return result.returncode
    finally:
        try:
            remove_agents_by_pid(os.getpid())
        except Exception:
            pass
