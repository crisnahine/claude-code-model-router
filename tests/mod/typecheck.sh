#!/bin/sh
# Type-checks the mod and its test against Claude Code's published mod API types.
# Everything it downloads goes to a temporary folder, so the working tree stays clean;
# --prefix keeps npm from installing into a parent folder that holds a package.json.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# TYPES_COMMIT=main checks against the newest published types instead of the pinned ones.
TYPES_COMMIT=${TYPES_COMMIT:-1006c2e4f783f016963fb290d3eb4afaa2d448b1}
curl -fsSL -o "$work/claude-code.d.ts" \
  "https://raw.githubusercontent.com/anthropics/claude-code/$TYPES_COMMIT/mods/types/claude-code.d.ts"
npm install --prefix "$work" --no-save --no-package-lock --no-audit --no-fund typescript@6.0.3 @types/node@22.20.5 >/dev/null
cat > "$work/tsconfig.json" <<EOF
{
  "extends": "$here/tsconfig.json",
  "compilerOptions": { "typeRoots": ["$work/node_modules/@types"] },
  "files": ["$work/claude-code.d.ts", "$here/../../plugins/model-router/hooks/router.ts", "$here/router.test.ts"]
}
EOF
"$work/node_modules/.bin/tsc" -p "$work/tsconfig.json"
# The field check reads the same types: no CI job has Claude Code's own copy, so it would skip there.
(cd "$here/.." && MODEL_ROUTER_MOD_TYPES="$work/claude-code.d.ts" python3 -m unittest test_model_router.ModApiTypes)
