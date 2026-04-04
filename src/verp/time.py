import time as _time


def now_ms() -> int:
    """Current time in milliseconds since epoch."""
    return int(_time.time() * 1000)
