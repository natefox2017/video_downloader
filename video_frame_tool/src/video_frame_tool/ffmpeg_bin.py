"""ffmpeg / ffprobe 定位：PATH → 本平台常见安装目录 → imageio-ffmpeg 自带二进制。"""

import os
import shutil

from .platform_compat import exe_name, ffmpeg_search_dirs

# ============================================================================
# 二、ffmpeg / ffprobe 定位
# ============================================================================

def _find_bin(name):
    """
    查找可执行文件，多级回退（全部按平台分支，不假设某一台机器）：

      1) 系统 PATH（shutil.which）
      2) 本平台常见的 ffmpeg 安装目录（见 ffmpeg_search_dirs）
      3) pip 包 imageio-ffmpeg 自带的二进制（三大平台的 wheel 都带，仅 ffmpeg 有）

    返回绝对路径；都找不到返回 None。
    """
    path = shutil.which(name) or shutil.which(exe_name(name))
    if path:
        return path
    for d in ffmpeg_search_dirs():
        cand = os.path.join(d, exe_name(name))
        if os.path.exists(cand):
            return cand
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return None


FFMPEG = _find_bin("ffmpeg")
FFPROBE = _find_bin("ffprobe")
