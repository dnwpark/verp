# NOTE: untested
import json
import shutil
import subprocess

from verp.focus._proc import pid_to_tty


class KittyFocuser:
    def available(self) -> bool:
        return shutil.which("kitten") is not None

    def focus(self, tty: str) -> bool:
        try:
            result = subprocess.run(
                ["kitten", "@", "ls"],
                capture_output=True,
                text=True,
                check=False,
            )
            windows = json.loads(result.stdout)
        except Exception:
            return False

        for os_window in windows:
            for tab in os_window.get("tabs", []):
                for window in tab.get("windows", []):
                    for proc in window.get("foreground_processes", []):
                        proc_pid = proc.get("pid")
                        if proc_pid and pid_to_tty(proc_pid) == tty:
                            win_id = window.get("id")
                            if win_id is None:
                                continue
                            subprocess.run(
                                [
                                    "kitten",
                                    "@",
                                    "focus-window",
                                    "--match",
                                    f"id:{win_id}",
                                ],
                                check=False,
                            )
                            return True

        return False
