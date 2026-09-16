#!/usr/bin/env bash
# 启动「视频批处理工具」图形界面（脚本就在项目根目录，双击/./run.sh 均可）。
#
#   ./run.sh                            正常启动（自动挑一个带 tkinter 的解释器）
#   PYTHON=/path/to/python3 ./run.sh     指定解释器（不做回退，缺 tkinter 直接报错）
#   VFT_DRY_RUN=1 ./run.sh               只打印最终会执行的命令，不启动界面
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

# ---------- 选一个真能用的解释器 ----------
# 不是「PATH 里第一个 python3」就够了：macOS 上 Homebrew 的 python3 常常没装
# python-tk，而 conda / 系统自带的 python3 反而有。所以逐个候选试 import tkinter，
# 挑第一个能用的；用户显式指定 PYTHON= 时不回退，直接用他给的那个。
STRICT=0
CANDS=()
add_cand() {
    [ -n "${1:-}" ] || return 0
    for existing in ${CANDS[@]+"${CANDS[@]}"}; do
        [ "$existing" = "$1" ] && return 0
    done
    CANDS+=("$1")
}

if [ -n "${PYTHON:-}" ]; then
    STRICT=1
    add_cand "$PYTHON"
else
    # 1) PATH 里的
    add_cand "$(command -v python3 2>/dev/null || true)"
    add_cand "$(command -v python 2>/dev/null || true)"
    # 2) 常见安装位置兜底（PATH 里没有、或那个没 tkinter 时）
    for d in /opt/homebrew/bin /usr/local/bin /opt/local/bin; do
        add_cand "$d/python3"
        add_cand "$d/python"
    done
    for d in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" /opt/miniconda3; do
        add_cand "$d/bin/python3"
    done
    for f in /opt/homebrew/Caskroom/miniconda/*/bin/python3; do
        add_cand "$f"
    done
    add_cand /usr/bin/python3
fi

PY=""
FAILED=""
for c in ${CANDS[@]+"${CANDS[@]}"}; do
    [ -x "$c" ] || continue
    if "$c" -c "import tkinter" >/dev/null 2>&1; then
        PY="$c"
        break
    fi
    FAILED="$FAILED
  - $c"
done

if [ -z "$PY" ]; then
    echo "没找到带 tkinter 的 Python 解释器。" >&2
    [ -n "$FAILED" ] && echo "试过这些，都缺 tkinter：$FAILED" >&2
    echo "修一下任意一条即可：" >&2
    echo "  macOS（Homebrew）：brew install python-tk" >&2
    echo "  macOS（conda）   ：conda install tk" >&2
    echo "  Debian/Ubuntu    ：sudo apt install python3-tk" >&2
    echo "  Fedora           ：sudo dnf install python3-tkinter" >&2
    echo "也可以直接指向一个已经能用的解释器：PYTHON=/path/to/python3 ./run.sh" >&2
    exit 1
fi

TKVER="$("$PY" -c 'import tkinter;print(tkinter.TkVersion)' 2>/dev/null || echo '?')"

# PATH 里第一个 python3 被跳过了就说一声，免得用户以为脚本没听他的
if [ "$STRICT" -eq 0 ]; then
    FIRST="$(command -v python3 2>/dev/null || true)"
    if [ -n "$FIRST" ] && [ "$FIRST" != "$PY" ]; then
        echo "提示：PATH 里的 python3（${FIRST}）缺少 tkinter，已自动改用 ${PY}。" >&2
        echo "      想固定下来：PYTHON=\"${PY}\" ./run.sh，或把上面的修复命令跑一次。" >&2
    fi
fi

# ffmpeg / ffprobe 缺失不阻断启动：工具自己还会去各平台常见目录和 imageio-ffmpeg 里找
for exe in ffmpeg ffprobe; do
    if ! command -v "$exe" >/dev/null 2>&1; then
        echo "提示：PATH 里没有 ${exe}，会改用工具内置的查找顺序。" >&2
    fi
done

echo "Python ：${PY}（tkinter ${TKVER}）"
echo "源码   ：${SRC}"

# VFT_DRY_RUN=1 ./run.sh —— 只打印最终会执行的命令，不真的启动界面（排查环境用）
if [ -n "${VFT_DRY_RUN:-}" ]; then
    printf '[dry-run] PYTHONPATH=%s %s -m video_frame_tool' "$SRC" "$PY"
    for a in ${1+"$@"}; do printf ' %s' "$a"; done
    printf '\n'
    exit 0
fi

exec env PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m video_frame_tool "$@"
exec env PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m video_frame_tool "$@"
