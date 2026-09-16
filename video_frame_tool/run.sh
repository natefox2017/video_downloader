#!/usr/bin/env bash
# 启动「视频批处理工具」图形界面（脚本就在项目根目录，双击/./run.sh 均可）。
#
#   ./run.sh            正常启动
#   PYTHON=/path/to/python3 ./run.sh    指定解释器
#
# 免安装运行：把本目录的 src 挂到 PYTHONPATH 再跑 -m video_frame_tool。
# 若已 pip install -e .，也可以直接用 video-frame-tool 命令，本脚本仍适用。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/src"

if [ ! -d "$SRC/video_frame_tool" ]; then
    echo "找不到源码目录：$SRC/video_frame_tool" >&2
    exit 1
fi

# 依次尝试 python3 / python；可用 PYTHON=/path/to/python3 ./run.sh 指定解释器
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PY="$(command -v "$candidate")"
            break
        fi
    done
fi
if [ -z "$PY" ]; then
    echo "找不到 python3，请先安装 Python 3.9+ 并确保在 PATH 里。" >&2
    exit 1
fi

# ffmpeg / ffprobe 缺失不阻断启动：工具自己还会去各平台常见目录和 imageio-ffmpeg 里找
for exe in ffmpeg ffprobe; do
    if ! command -v "$exe" >/dev/null 2>&1; then
        echo "提示：PATH 里没有 $exe，会改用工具内置的查找顺序。" >&2
    fi
done

# tkinter 缺失时给出可执行的修复命令，而不是让 Python 抛一大段 traceback
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    echo "当前解释器缺少 tkinter：$PY" >&2
    echo "  macOS（Homebrew）：brew install python-tk" >&2
    echo "  Debian/Ubuntu：sudo apt install python3-tk" >&2
    echo "也可以指定另一个解释器：PYTHON=/path/to/python3 ./run.sh" >&2
    exit 1
fi

echo "Python ：$PY"
echo "源码   ：$SRC"
exec env PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m video_frame_tool "$@"
