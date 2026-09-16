"""媒体信息探测。

ffprobe 解析、时长/尺寸/帧率、手机竖屏的 rotation 处理、目录扫描（视频与图片）。
注意：ffprobe 的 width/height 是**存储尺寸**，横竖判断必须按 rotation 交换。"""

import json
from functools import lru_cache
import os
import re
import subprocess
import threading

from .constants import IMAGE_EXTS, VIDEO_EXTS
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


def probe_duration(path):
    """
    轻量版探测：只取时长。
    素材池扫描时用它比 probe_media 快很多（输出极小，解析成本低）。
    """
    if FFPROBE:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60)
        try:
            d = float((out.stdout or "").strip().split(",")[0])
            if d > 0:
                return d
        except Exception:
            pass
    return probe_media(path)["duration"]


def probe_dur_size(path):
    """
    一次 ffprobe 同时取回「时长 + 显示宽高」（素材池扫描专用）。

    仍是**一次**进程调用，只是多要了两个字段：素材池里这三项都要用——
    时长决定切成多少段，宽高决定低清副本该生成多大。

    注意这里返回的是**显示尺寸**：手机竖屏视频常见「存储 1920x1080 + 旋转 90°」，
    ffprobe 的 width/height 报的是存储尺寸，而 ffmpeg 解码时会按元数据自动摆正。
    不换算的话横屏/竖屏判断会反过来，副本尺寸就跟着错
    （本机素材池实测：229/402 个属于这种情况）。

    :return: (duration, width, height)，取不到的项为 0
    """
    if FFPROBE:
        try:
            out = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries",
                 "stream=width,height:stream_side_data=rotation:format=duration",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=60)
            data = json.loads(out.stdout or "{}")
            st = (data.get("streams") or [{}])[0]
            w = int(st.get("width") or 0)
            h = int(st.get("height") or 0)
            dur = float((data.get("format") or {}).get("duration") or 0.0)
            for sd in (st.get("side_data_list") or []):
                try:
                    if abs(int(sd.get("rotation") or 0)) % 180 == 90:
                        w, h = h, w                  # 旋转 90°：显示尺寸是存储尺寸转置
                        break
                except (TypeError, ValueError):
                    continue
            if w and h and dur > 0:
                return dur, w, h
        except Exception:
            pass
    info = probe_media(path)                 # 兜底：走完整探测
    return info["duration"], info["width"], info["height"]


_IMAGE_PROBE_LOCK = threading.Lock()


def probe_image_size(path):
    """按路径、体积及纳秒修改时间复用图片尺寸；并发抽到同一张图只探测一次。"""
    path = os.path.abspath(path)
    stat = os.stat(path)
    with _IMAGE_PROBE_LOCK:
        return _probe_image_size_cached(path, stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=256)
def _probe_image_size_cached(path, size, mtime_ns):
    """只缓存尺寸，不缓存像素；文件指纹变化自动失效，最多保留 256 条。"""
    streams = (_probe_json(path) if FFPROBE else _probe_fallback(path)).get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v or not v.get("width"):
        raise RuntimeError(f"无法读取图片尺寸: {path}")
    return int(v["width"]), int(v["height"])


def _even(n):
    """向下取最近的偶数并保证 >= 2（yuv420p 要求宽高为偶数）"""
    n = int(round(n))
    return max(2, n if n % 2 == 0 else n - 1)


def _list_videos(folder):
    """列出目录内的视频文件（不递归），返回排序后的绝对路径列表"""
    try:
        with os.scandir(folder) as entries:
            return sorted(entry.path for entry in entries
                          if os.path.splitext(entry.name)[1].lower() in VIDEO_EXTS
                          and entry.is_file())
    except Exception:
        return []


def list_images(path):
    """
    把用户给的路径解析成「可选图片列表」。

    用途：首图与主图都支持"给一个目录，每条视频从里面随机取一张"。
      - 传目录    ：列出目录内所有图片，按文件名排序（顺序稳定，便于排查）
      - 传单个文件：就是它自己（兼容以前"只指定一张图"的用法）
      - 路径不存在 / 目录里没图片：返回空列表，由调用方决定怎么提示

    只看图片后缀的文件，忽略子目录和其它文件（目录里的 .DS_Store 之类不会被抽到）。
    """
    if not path:
        return []
    try:
        if os.path.isfile(path):
            return [path]
        if not os.path.isdir(path):
            return []
        return [os.path.join(path, f) for f in sorted(os.listdir(path))
                if os.path.splitext(f)[1].lower() in IMAGE_EXTS
                and os.path.isfile(os.path.join(path, f))]
    except Exception:
        return []
