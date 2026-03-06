#!/bin/sh
trap '' HUP
DATA=$(cat)
eval "$(printf '%s' "$DATA" | python3 -c "
import sys, json, shlex, time
d = json.load(sys.stdin)
print('SESSION=' + shlex.quote(d.get('session_id', '')))
print('EVENT=' + shlex.quote(d.get('hook_event_name', '')))
print('TOOL=' + shlex.quote(d.get('tool_name') or ''))
print('TS=' + str(int(time.time() * 1000)))
")"

[ -z "$SESSION" ] && exit 0

case "$EVENT" in
  SessionStart)
    verp _claude hook_session_start "$SESSION" "$TS"
    ;;
  SessionEnd)
    verp _claude hook_session_end "$SESSION" "$TS"
    ;;
  PreToolUse)
    verp _claude hook_pre_tool_use "$SESSION" "$CLAUDE_PROJECT_DIR" "$TOOL" "$TS"
    ;;
  PostToolUse)
    verp _claude hook_post_tool_use "$SESSION" "$CLAUDE_PROJECT_DIR" "$TOOL" "$TS"
    ;;
  PostToolUseFailure)
    verp _claude hook_post_tool_use_failure "$SESSION" "$CLAUDE_PROJECT_DIR" "$TOOL" "$TS"
    ;;
  PermissionRequest)
    printf '%s' "$DATA" | verp _claude hook_permission_request "$SESSION" "$CLAUDE_PROJECT_DIR" "$TOOL" "$TS"
    ;;
  UserPromptSubmit)
    verp _claude hook_user_prompt_submit "$SESSION" "$CLAUDE_PROJECT_DIR" "$TS"
    ;;
  Stop)
    verp _claude hook_stop "$SESSION" "$CLAUDE_PROJECT_DIR" "$TS"
    ;;
esac
