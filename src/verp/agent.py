import time


def format_age(updated_at: int) -> str:
    secs = (int(time.time() * 1000) - updated_at) // 1000
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"
