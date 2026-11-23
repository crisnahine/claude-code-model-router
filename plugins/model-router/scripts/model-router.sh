#!/bin/sh
# Launcher for model-router.py, used by hooks and skills.
# Runs the first Python 3.9+ interpreter on PATH. model-router.py checks its own version
# and exits 87, before reading stdin, when the interpreter is too old, so a working run
# costs one interpreter start. Any other failure is checked with a version probe: a real
# error from a command keeps its exit code, while a broken interpreter (a Store stub, a
# shim with no version set) gives the next one a turn. If none works, it exits 0: a missing
# interpreter must never block a session, a tool call or a skill. The plugin's hooks are
# then inactive, and skills say why. A hook's error exits 1, never 2: exit 2 blocks the tool
# call, the prompt or the stop (https://code.claude.com/docs/en/hooks.md#exit-code-2-behavior-per-event).
dir=$(dirname "$0")
hook=
case " $* " in
  *" session-start "*|*" user-prompt "*|*" post-edit "*|*" agent-failed "*|*" agent-stopped "*|*" stop "*|*" pre-agent "*|*" post-agent "*|*" model-switch "*|*" refresh-cache "*|*" mod-start "*|*" mod-spawn "*|*" mod-turn "*|*" mod-usage "*)
    hook=1 ;;
esac
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    "$py" "$dir/model-router.py" "$@"
    code=$?
    [ "$code" -eq 0 ] && exit 0
    if [ "$code" -ne 87 ] && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
      [ -n "$hook" ] && exit 1
      exit "$code"
    fi
  fi
done
if [ -n "$hook" ]; then
  [ -t 0 ] || cat >/dev/null
else
  echo "(Model Router's helper needs Python 3.9 or later on PATH, so this information is unavailable.)"
fi
exit 0
