#!/usr/bin/env bash
# 启动「视频批处理工具」图形界面（脚本就在项目根目录，双击/./run.sh 均可）。
#
#   ./run.sh                            正常启动（直接用当前环境变量里的 Python）
#   PYTHON=/path/to/python3 ./run.sh     指定解释器
#   VFT_DRY_RUN=1 ./run.sh               只打印最终会执行的命令，不启动界面
#
# 免安装运行：把项目根挂到 PYTHONPATH 再跑 -m src（包在 src/ 下）。
# 若已 pip install -e .，也可以直接用 video-frame-tool 命令，本脚本仍适用。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$ROOT/src/__init__.py" ]; then
    echo "找不到包入口：$ROOT/src/__init__.py" >&2
    exit 1
fi

# ---------- 直接用当前环境里的 Python ----------
# 只认 PATH 里的 python3 / python（PYTHON= 显式指定则只认它），
# 不再去各种安装位置翻兜底。哪个能 import tkinter 就用哪个，不刷提示。
STRICT=0
CANDS=()
if [ -n "${PYTHON:-}" ]; then
    STRICT=1
    CANDS=("$PYTHON")
else
    add_cand() {
        local p
        p="$(command -v "$1" 2>/dev/null || true)"
        [ -n "$p" ] || return 0
        local c
        for c in ${CANDS[@]+"${CANDS[@]}"}; do
            [ "$c" = "$p" ] && return 0
        done
        CANDS+=("$p")
    }
    add_cand python3
    add_cand python
fi

PY=""
for c in ${CANDS[@]+"${CANDS[@]}"}; do
    if [ -x "$c" ] && "$c" -c "import tkinter" >/dev/null 2>&1; then
        PY="$c"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "当前环境的 Python 没有 tkinter，起不了界面。" >&2
    echo "装一下任选其一：brew install python-tk ｜ conda install tk" >&2
    echo "或指定一个带 tkinter 的：PYTHON=/path/to/python3 ./run.sh" >&2
    exit 1
fi

# ffmpeg / ffprobe 缺失不阻断启动：工具自己还会去各平台常见目录和 imageio-ffmpeg 里找
for exe in ffmpeg ffprobe; do
    if ! command -v "$exe" >/dev/null 2>&1; then
        echo "提示：PATH 里没有 ${exe}，会改用工具内置的查找顺序。" >&2
    fi
done

if [ -n "${VFT_DRY_RUN:-}" ]; then
    printf '[dry-run] PYTHONPATH=%s %s -m src' "$ROOT" "$PY"
    for a in ${1+"$@"}; do printf ' %s' "$a"; done
    printf '\n'
    exit 0
fi

exec env PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m src "$@"
