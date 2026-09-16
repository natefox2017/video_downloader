"""硬件加速**解码**参数。

只做解码、且分阶段启用（见 HW_AUTO_STAGES 与 README 的实测表）。
本工具**不提供硬件编码**：h264_videotoolbox 批量场景只有 1.06x，不值得切。"""

import sys

from .constants import DEFAULT_HWACCEL, HWACCEL_AUTO, HWACCEL_OFF, HWACCEL_ON, HW_AUTO_STAGES, HW_STAGE_COPY, HW_STAGE_MAIN

def hw_decode_args(mode=None, stage=HW_STAGE_MAIN):
    """
    返回放在**视频输入** -i 之前的硬件解码参数；空列表 = 用 ffmpeg 默认（软件解码）。

    只应加在视频输入上：首图 / 主图是单张 PNG/JPG，走硬解没有意义还可能失败。

    实测（本机 Apple Silicon，每种配置连跑 3 轮取中位数）——**硬解不是处处有用**：

        阶段                              软解        硬解        结论
        生成低清副本（解码 1080p 原片）    4.5 个/秒   5.7 个/秒   快 26% → 自动开
        画中画片段编码（解码 480p 副本×8）  0.65 秒    1.04 秒     慢 60% → 自动关
        主合成（1 主视频 + 2 图 + 1 轨）    5.8 秒     5.8 秒      无差别 → 自动关

    原因：硬解只在"解码量真的很大"时占便宜（1080p/4K 原片）。素材换成 480p 副本后
    解码本来就只要 0.65 秒，硬解的固定开销 + 把帧从 GPU 搬回内存反而成了大头。

    硬件**编码**则一律不用：把 h264_videotoolbox 的各种调法都试过
    （-q:v 45/55/65、-realtime 1、-b:v 6M、带/不带 -pix_fmt、再叠加硬解），
    全部落在 0.72~0.78 倍，比 libx264 veryfast 慢，体积还更大。
    原因是滤镜链跑在 CPU 上，硬编要把每一帧搬进搬出 GPU。

    所以："自动" = 只在该用的阶段用（见 HW_AUTO_STAGES）；
          "开启" = 所有阶段都强行开（换了显卡/换了素材形态时给自己留的试错口子）；
          "关闭" = 一律软解。

    :param mode : HWACCEL_AUTO / HWACCEL_ON / HWACCEL_OFF，None 表示用默认
    :param stage: HW_STAGE_* 之一，见上面的实测表
    """
    m = mode or DEFAULT_HWACCEL
    if m == HWACCEL_OFF:
        return []
    if m == HWACCEL_AUTO and stage not in HW_AUTO_STAGES:
        return []                        # 实测这些阶段用硬解没有收益，甚至更慢
    if sys.platform == "darwin":
        return ["-hwaccel", "videotoolbox"]
    if m == HWACCEL_ON:
        return ["-hwaccel", "auto"]      # Windows/Linux：交给 ffmpeg 自己挑 cuda / qsv / vaapi
    return []                            # 非 macOS 的"自动"保持原样，不在没验证过的机器上冒险


def _small_accel_args(mode=None):
    """
    素材副本转码用的「解码」参数（就是 hw_decode_args 的 COPY 阶段）。

    瓶颈全在"解码 1080p 素材"上，不在编码，所以这里只调解码侧。
    实测（24 个 1080p 竖屏素材、4 并发）：
        默认（不限线程）            1.6 个/秒   ← 每个进程的解码器都吃满核心，互相踩
        -threads 1（限制解码线程）  4.5 个/秒
        -hwaccel videotoolbox      5.7 个/秒   ← macOS 硬件解码，再快 26%
    """
    args = hw_decode_args(mode, HW_STAGE_COPY)
    return args if args else ["-threads", "1"]
