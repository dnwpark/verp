import subprocess


def _focus_via_osascript(tty: str) -> bool:
    script = f"""
tell application "Terminal"
    repeat with w in windows
        repeat with t in tabs of w
            if tty of t is "{tty}" then
                set selected of t to true
                set index of w to 1
                activate
                return
            end if
        end repeat
    end repeat
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


class MacOSFocuser:
    def available(self) -> bool:
        return True

    def focus(self, tty: str) -> bool:
        return _focus_via_osascript(tty)
