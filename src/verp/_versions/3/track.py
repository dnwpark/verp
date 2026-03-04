#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import time

project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
db_path = os.path.join(project_dir, ".claude", "verp.db")

data = json.load(sys.stdin)
event = data.get("hook_event_name", "")
session_id = data.get("session_id", "")

if not session_id:
    sys.exit(0)

conn = sqlite3.connect(db_path)
conn.execute(
    "CREATE TABLE IF NOT EXISTS agents ("
    "    session_id TEXT PRIMARY KEY,"
    "    status     TEXT NOT NULL,"
    "    tool       TEXT,"
    "    updated_at INTEGER NOT NULL"
    ")"
)

now = int(time.time())

if event == "SessionEnd":
    conn.execute("DELETE FROM agents WHERE session_id = ?", (session_id,))
elif event == "PreToolUse":
    tool = data.get("tool_name")
    conn.execute(
        "INSERT INTO agents (session_id, status, tool, updated_at)"
        " VALUES (?, 'working', ?, ?)"
        " ON CONFLICT(session_id) DO UPDATE SET"
        "     status = 'working', tool = excluded.tool, updated_at = excluded.updated_at",
        (session_id, tool, now),
    )
elif event == "PostToolUse":
    conn.execute(
        "INSERT INTO agents (session_id, status, tool, updated_at)"
        " VALUES (?, 'working', NULL, ?)"
        " ON CONFLICT(session_id) DO UPDATE SET"
        "     status = 'working', tool = NULL, updated_at = excluded.updated_at",
        (session_id, now),
    )
elif event == "PermissionRequest":
    conn.execute(
        "INSERT INTO agents (session_id, status, tool, updated_at)"
        " VALUES (?, 'waiting_permission', NULL, ?)"
        " ON CONFLICT(session_id) DO UPDATE SET"
        "     status = 'waiting_permission', tool = NULL, updated_at = excluded.updated_at",
        (session_id, now),
    )
else:
    # SessionStart, Stop
    conn.execute(
        "INSERT INTO agents (session_id, status, tool, updated_at)"
        " VALUES (?, 'waiting_prompt', NULL, ?)"
        " ON CONFLICT(session_id) DO UPDATE SET"
        "     status = 'waiting_prompt', tool = NULL, updated_at = excluded.updated_at",
        (session_id, now),
    )

conn.commit()
conn.close()
