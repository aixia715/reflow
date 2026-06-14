#!/usr/bin/env bash
# 将 deploy.sh 的非配置部分同步到所有 deploy.*.sh

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
TEMPLATE="deploy.sh"

sync_one() {
  local target="$1"
  python3 - "$TEMPLATE" "$target" <<'PYEOF'
import sys

def split(lines):
    start = next(i for i, l in enumerate(lines) if '配置' in l and l.startswith('# ─'))
    end   = next(i for i, l in enumerate(lines) if i > start and l.startswith('# ─') and '配置' not in l)
    return lines[:start], lines[start:end+1], lines[end+1:]

tmpl = open(sys.argv[1]).readlines()
tgt  = open(sys.argv[2]).readlines()

before, _,   after = split(tmpl)
_,      cfg, _     = split(tgt)

open(sys.argv[2], 'w').writelines(before + cfg + after)
print(f"  已同步: {sys.argv[2]}")
PYEOF
}

count=0
for f in deploy.*.sh; do
  [ -f "$f" ] || continue
  sync_one "$f"
  count=$((count + 1))
done

if [ "$count" -eq 0 ]; then echo "未找到 deploy.*.sh 文件"; fi
