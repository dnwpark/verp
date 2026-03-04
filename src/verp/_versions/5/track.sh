#!/bin/sh
DATA=$(cat)
eval "$(printf '%s' "$DATA" | python3 -c "
import sys, json, shlex
d = json.load(sys.stdin)
print('SESSION=' + shlex.quote(d.get('session_id', '')))
print('EVENT=' + shlex.quote(d.get('hook_event_name', '')))
print('TOOL=' + shlex.quote(d.get('tool_name') or ''))
")"

[ -z "$SESSION" ] && exit 0

case "$EVENT" in
  SessionStart)
    ;;
  SessionEnd)
    verp _internal agent_remove "$SESSION"
    ;;
  PreToolUse)
    verp _internal agent_event "$SESSION" "$CLAUDE_PROJECT_DIR" working "$TOOL"
    ;;
  PostToolUse)
    verp _internal agent_event "$SESSION" "$CLAUDE_PROJECT_DIR" working
    ;;
  PermissionRequest)
    verp _internal agent_event "$SESSION" "$CLAUDE_PROJECT_DIR" waiting_permission
    ;;
  *)
    verp _internal agent_event "$SESSION" "$CLAUDE_PROJECT_DIR" waiting_prompt
    ;;
esac
