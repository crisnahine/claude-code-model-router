#!/bin/sh
# Launcher for orchestrator.py, used by hooks and skills.
# Finds a Python 3.9+ interpreter. If there is none, it exits 0: a missing
# interpreter must never block a session, a tool call or a skill. The plugin's
# hooks are then inactive, and skills say why.
dir=$(dirname "$0")
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    exec "$py" "$dir/orchestrator.py" "$@"
  fi
done
case " $* " in
  *" session-start "*|*" user-prompt "*|*" post-edit "*|*" stop "*|*" pre-agent "*|*" post-agent "*|*" model-switch "*|*" refresh-cache "*)
    [ -t 0 ] || cat >/dev/null ;;
  *) echo "(Orchestrator's helper needs Python 3.9 or later on PATH, so this information is unavailable.)" ;;
esac
exit 0
