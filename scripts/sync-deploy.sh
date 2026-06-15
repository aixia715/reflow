#!/usr/bin/env bash
# 同步 / 校验 deploy.sh 的非配置部分到 deploy.*.sh
#   无参数        ：把 deploy.sh 的非配置部分同步到所有 deploy.*.sh（保留各自配置块）
#   --check FILE  ：仅校验 FILE 与 deploy.sh 的非配置部分是否一致（一致 exit 0，不一致 exit 1）

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
TEMPLATE="deploy.sh"

# run_py <mode:sync|check> <target>
run_py() {
  python3 - "$1" "$TEMPLATE" "$2" <<'PYEOF'
import sys

mode, tmpl_path, tgt_path = sys.argv[1], sys.argv[2], sys.argv[3]

def split(lines):
    start = next(i for i, l in enumerate(lines) if '配置' in l and l.startswith('# ─'))
    end   = next(i for i, l in enumerate(lines) if i > start and l.startswith('# ─') and '配置' not in l)
    return lines[:start], lines[start:end+1], lines[end+1:]

tmpl = open(tmpl_path).readlines()
tgt  = open(tgt_path).readlines()

tbefore, _,    tafter = split(tmpl)
gbefore, gcfg, gafter = split(tgt)

if mode == 'check':
    sys.exit(0 if (tbefore + tafter) == (gbefore + gafter) else 1)

open(tgt_path, 'w').writelines(tbefore + gcfg + tafter)
print(f"  已同步: {tgt_path}")
PYEOF
}

if [ "${1:-}" = "--check" ]; then
  if [ -z "${2:-}" ]; then echo "用法：sync-deploy.sh --check <file>" >&2; exit 2; fi
  if run_py check "$2"; then exit 0; else exit 1; fi
fi

count=0
for f in deploy.*.sh; do
  [ -f "$f" ] || continue
  run_py sync "$f"
  count=$((count + 1))
done

if [ "$count" -eq 0 ]; then echo "未找到 deploy.*.sh 文件"; fi
