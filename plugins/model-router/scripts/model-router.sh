#!/bin/sh
# Runs model-router.py for hooks and skills on the first working Python 3.9+ on PATH (87: too old, try the next);
# with none it exits 0. A hook's own error exits 1, never 2, since 2 blocks the tool call, prompt or stop.
# -I: a repository's settings can set PYTHONPATH or PYTHONHOME for hooks, which must not load code here.
dir=$(dirname "$0")
hook=
# The command is the first argument after --data-dir; a hook name later on, as in `explain ... stop`, is text.
cmd=$1
case "$cmd" in
  --data-dir) cmd=${3-} ;;
  --data-dir=*) cmd=${2-} ;;
esac
case "$cmd" in
  session-start|user-prompt|post-edit|agent-failed|agent-stopped|stop|pre-agent|post-agent|model-switch|mod-start|mod-spawn|mod-usage|mod-status|mod-check|mod-end)
    hook=1 ;;
esac
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    "$py" -I "$dir/model-router.py" "$@"
    code=$?
    [ "$code" -eq 0 ] && exit 0
    if [ "$code" -ne 87 ] && "$py" -I -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
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
