#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "未找到 $PYTHON，请先创建 Open WebUI 虚拟环境。" >&2
  exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/run_batch.py"
