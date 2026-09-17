"""媒体信息探测。

ffprobe 解析、时长/尺寸/帧率、手机竖屏的 rotation 处理、目录扫描（视频与图片）。
注意：ffprobe 的 width/height 是**存储尺寸**，横竖判断必须按 rotation 交换。"""

import json
import os
import re
import subprocess

from .constants import VIDEO_EXTS
from .ffmpeg_bin import FFMPEG, FFPROBE

# ============================================================================
# 三、媒体信息探测
# ============================================================================

def _parse_fps(raw):
    """
    把 ffprobe 的帧率表示解析成 (滤镜可用字符串, 浮点值)。
    输入形如 "30000/1001"（分数）或 "30"（整数），非法时回退 25fps。
    """
    try:
        if "/" in str(raw):
            num, den = str(raw).split("/")
            num, den = float(num), float(den)
            if num > 0 and den > 0:
                return (f"{int(num)}/{int(den)}" if num % 1 == 0 else f"{num}/{den}"), num / den
    except Exception:
        pass
    try:
        v = float(raw)
        if v > 0:
            return f"{v}", v
    except Exception:
        pass
    return "25", 25.0


def _probe_json(path):
    """用 ffprobe 一次性取出视频流 / 音频流 / 时长的 JSON 信息（单次进程调用）"""
    cmd = [FFPROBE, "-v", "error", "-show_entries",
           "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration"
           ":format=duration",
           "-of", "json", path]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout or "{}")


def _probe_fallback(path):
    """没有 ffprobe 时的兜底方案：解析 `ffmpeg -i` 的 stderr 文本"""
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", path],
                         capture_output=True, text=True, timeout=120).stderr
    streams, fmt = [], {}
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if m:
        f = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
        streams.append({"codec_type": "video", "width": int(m.group(1)),
                        "height": int(m.group(2)), "r_frame_rate": f.group(1) if f else "25"})
    am = re.search(r"Audio:\s*([A-Za-z0-9_]+)", err)
    if am:
        streams.append({"codec_type": "audio", "codec_name": am.group(1)})
    dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if dm:
        fmt["duration"] = str(int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3)))
    return {"streams": streams, "format": fmt}


def probe_media(path):
    """
    探测视频基本信息。

    返回 dict：
        width/height  像素尺寸
        fps_str       帧率字符串（可直接写进 fps= 滤镜）
        fps           帧率浮点值
        duration      时长（秒），取不到为 0
        has_audio     是否含音频
        acodecs       音频编码名列表（用于决定 copy 还是重编码）
    """
    data = _probe_json(path) if FFPROBE else _probe_fallback(path)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v or not v.get("width"):
        raise RuntimeError("未找到视频流")
    fps_raw = v.get("r_frame_rate") or v.get("avg_frame_rate") or "25"
    if fps_raw in ("0/0", "0", ""):
        fps_raw = v.get("avg_frame_rate") or "25"
    fps_str, fps_val = _parse_fps(fps_raw)

    # 时长依次尝试：容器 format.duration → 视频流 duration → 帧数/帧率
    duration = 0.0
    for cand in (data.get("format", {}).get("duration"), v.get("duration")):
        try:
            if cand and float(cand) > 0:
                duration = float(cand)
                break
        except Exception:
            continue
    if duration <= 0 and v.get("nb_frames"):
        try:
            duration = float(v["nb_frames"]) / fps_val
        except Exception:
            duration = 0.0

    acodecs = [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"]
    return {"width": int(v["width"]), "height": int(v["height"]),
            "fps_str": fps_str, "fps": fps_val, "duration": duration,
            "has_audio": bool(acodecs), "acodecs": [c for c in acodecs if c]}


def _list_videos(folder):
    """列出目录内的视频文件（不递归），返回排序后的绝对路径列表"""
    try:
        with os.scandir(folder) as entries:
            return sorted(entry.path for entry in entries
                          if os.path.splitext(entry.name)[1].lower() in VIDEO_EXTS
                          and entry.is_file())
    except Exception:
        return []
