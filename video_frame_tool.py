#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 视频批处理工具（GUI）—— 短视频二次加工流水线
===============================================================================

【一句话说明】
    把一个文件夹里的视频批量做四件事：换首帧、贴产品图、右上加随机拼接的画中画，
    然后输出到源目录下的 out/ 里。

【四项功能】
    1. 首帧替换：从「首图目录」里随机取一张，替换每个视频的第 1 帧
                 （每条视频固定用抽中的那一张，全程不换）
    2. 主图叠加：从「主图目录」里随机取一张，叠在视频中下方，距底部 20px、
                 水平居中、宽度 = 视频宽度 1/4，从第几帧开始显示可设置
                 （默认第 60 帧，填 0 表示首帧就显示）
                 **可选**：界面上的「显示概率」决定每条视频叠不叠——
                 100 = 每条必叠（默认），0 = 完全不叠，40 = 40% 概率叠；
                 每条视频独立掷骰（见 _decide_product），概率 0 时主图目录可留空
    3. 批量处理：扫描所选目录内的视频，多线程并发，输出到 <视频目录>/out/
    4. 画中画  ：从「画中画目录」随机抽取互不重复的片段，拼接成一条静音轨，
                 播放速度可调，叠在画面右上角，总长度自动对齐主视频
                 **可选**：界面「启用画中画」开关关掉后，既不拼画中画、
                 也不生成低清副本（进度表自动只算合成一段，见 VideoProgress）

【界面能看到什么】
    总进度只吃真实数字（低清副本 15% / 画中画 25% / 合成 60%），按每条视频的时长加权，
    取值为只增不减，不做任何估算动画；处理按钮运行时显示「处理中…」并禁用。
    资源行显示整机 CPU 和整机内存，每秒采样，不将 Python RSS 当成工具总占用。
    日志仅保留批次结果与必要异常，每批清空，最多 20 行。
    「打开副本目录」直接打开当前画中画目录里的 .pip_small，不创建空缓存。

【画中画素材的"去重/抗查重"设计】（关键，改动前务必理解）
    平台查重主要看画面哈希与镜头序列，因此本工具在素材层做多重随机化：
      a) 素材选择随机  ：每次从素材池随机洗牌，同一轮内不重复使用同一个文件
      b) 片段起点随机  ：在素材的"有效区间"内随机取起点
      c) 片段长度随机  ：每段 4~12 秒（SEG_MIN / SEG_MAX 可调）
      d) 掐头去尾      ：默认丢弃每个素材前 10% 与后 10%（避开片头片尾/水印/黑场）
      e) 播放加速      ：默认 1.2 倍（画面节奏变化，帧序列完全不同）
      f) 随机水平镜像翻转
      g) 随机轻微缩放（1.00~1.06 倍，再裁回固定尺寸，画面像素发生位移）
      h) 随机轻微调色（亮度/对比度/饱和度微扰，肉眼几乎无感但改变像素值）
      i) 画中画位置随机微抖动（±3px）

【画中画轨道必须统一色彩空间】（2026-09-16 修复，改动滤镜链前必读）
    素材池混着三种色彩空间：bt709、bt2020nc + arib-std-b67（手机 HLG）、bt470bg（BT.601）。
    不统一就把它们拼进同一条画中画轨，ffmpeg 会在每个色彩切换点
    "Reconfiguring filter graph because video parameters changed"，主合成的帧时间戳
    随之错乱、大量丢帧，成片视频轨只剩源的一半（例：video 161.97s/4853 帧 而
    audio 323.22s/13920 帧）。现象是播放到两分钟左右画面定格、只剩声音、音画错位；
    并发处理时 100% 复现，单条干净素材的轨道则不会出现。
    因此 _clip_filter() 的每个 scale 都带 ":out_color_matrix=bt709"（**真正转换像素
    矩阵**），链末再用 setparams 统一标记；_encode_pip_batch() 同时写容器级
    -colorspace/-color_primaries/-color_trc bt709。
    **只写 setparams 不改像素会让 BT.601 素材偏色**（回归测试里绿画中画的像素断言会失败）。
    这个故障是"静默丢帧"：文件能播、时长正常，只能靠 ffprobe 对比视频轨与音轨帧数发现。

【性能设计】（改代码时请保持这些约束，否则会又慢又吵）
    0. 画中画素材先降成"低清副本"再拼接（本项目收益最大的一项，详见
       PIP_SMALL_DIR 上方注释）：画中画在成片里只占约 260x384 像素，却要解码
       1080p 原片，画中画阶段 93% 的时间与 86% 的内存都耗在这上面。
       **副本是长期缓存、生成后不删**：每次处理前先按台账核对，已生成的直接
       调用（只 stat，不解码不转码，402 个素材核对一次 0.05 秒），
       只有缺的 / 素材改了 / 画中画调大了的才新建。同一素材目录的核对和
       生成共用一把锁，避免并发主视频/后台预热重复转码并争用临时文件与台账；
       锁内仍按 PIP_SMALL_THREADS 并行生成不同素材。
    1. 素材池元数据缓存：目录内有 400+ 素材时，逐个 ffprobe 会非常慢。
       首次扫描结果（时长 + 文件指纹）落盘到 <素材目录>/.pip_cache.json，
       下次启动校验指纹后直接复用；进程内还有一层内存缓存，
       多个视频并发时只有第一个真正扫描。
    2. 素材扫描并行：首次扫描用 16 路线程池，后续只探测新增/修改文件。
       图片尺寸按文件指纹缓存，首图/主图目录每批只列一次。
       热缓存不重写台账，规格升级发布新版本，避免后续每次都重建。
    3. 编码线程配额：按"实际同时处理的视频数"分配，每个 ffmpeg 拿到
       max(1, CPU核数 // 实际并发数) 个线程。**不能用界面上填的并发数**：
       只处理 2 个文件却填了 5，若按 5 分配，2 个文件只能用到 CPU核数/5 的算力，
       会白白慢好几倍（这是实测踩过的坑）。
    4. 画中画片段批量编码：把 PIP_BATCH 个片段放进**同一个 ffmpeg 进程**
       （多输入 + concat 滤镜）一次编完，批次之间并行。
       早期实现是"一段一个进程、串行跑"，9 分钟视频要起 100+ 次 ffmpeg，
       进程启停开销比编码本身还大，是当时慢和风扇狂转的主因。
    5. 画中画轨道拼接用 stream copy：各批参数完全一致，可直接 -c copy 拼接，
       不再解码重编码。
    6. 日志限流：Text 控件保留最后 LOG_MAX_LINES 行，且每轮批量插入一次，
       避免高频刷新拖慢 Tk 主线程；同轮状态和进度也合并刷新。
    7. 单次成片只编码两遍：画中画片段一次 + 最终合成一次（首帧/产品图/画中画
       全部在同一条 filter_complex 里完成）。
       总进度用 VideoProgress 折出的真实完成度、按视频时长加权，不做估算。
       副本目录按钮复用 small_dir_of()，不创建或删除缓存。
    8. 系统资源监控每秒采样一次，用的都是各平台的原生廉价接口（微秒级），
       不引入 psutil 等第三方依赖，也不做任何可能阻塞界面的操作。
    9. 硬件加速只做解码，且分阶段启用（见 hw_decode_args 的实测表与
       HW_AUTO_STAGES）。**不提供硬件编码**：7 种调法实测全部比 libx264
       veryfast 慢，因为滤镜链跑在 CPU 上，硬编要把每帧搬进搬出 GPU。
    10. 新任务开始时取消后台预热的剩余工作；已启动的副本生成完成后释放目录锁。
        停止后不再逐段重试或启动主合成，共享素材列表只在任务副本里洗牌。

【跨平台与配置记忆】（改代码前必读）
    本工具在 Windows / macOS / Linux 上均可运行，代码中**不写死任何素材路径**，
    也不假设运行在哪一台机器上。所有平台差异集中在文件开头的"零、平台适配层"：
        配置目录   Windows=%APPDATA% / macOS=~/Library/Application Support / Linux=$XDG_CONFIG_HOME
        打开目录   Windows=startfile / macOS=open / Linux=xdg-open
        等宽字体   Windows=Consolas / macOS=Menlo / Linux=DejaVu Sans Mono
        ffmpeg 搜索 Windows=常见安装目录 / macOS=Homebrew / Linux=/usr/bin 等
    用户上次选择的路径与各项参数记录在 settings.json（位置见上表），
    下次启动自动回填；路径失效时只在日志里提示，不阻断使用。

【依赖】
    ffmpeg / ffprobe：优先 PATH，其次本平台常见安装目录，
    最后回退 pip 安装的 imageio-ffmpeg 自带二进制。没有 ffprobe 时用 ffmpeg -i 兜底解析。

【运行】
    python3 video_frame_tool.py
===============================================================================
"""

import os
import re
import sys
import json
import zlib           # 程序图标回退用：纯标准库解内嵌 PNG（见"一·七"）
import struct         # 同上：解析 PNG 分块头
import ctypes          # 系统资源监控用：调用各平台原生 API（macOS mach / Windows Win32）
import ntpath          # 用于生成规范的 Windows 路径（跨平台可用）
import queue
import random
import shutil
import tempfile
import threading
import subprocess
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from functools import lru_cache
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

# ============================================================================
# 零、平台适配层（跨平台支持：Windows / macOS / Linux）
# ============================================================================
# 设计说明（交接必读）：
#   本工具不假设运行在某一台特定机器上，而是显式按"操作系统类型"分支处理。
#   凡是与操作系统相关的差异（配置目录位置、打开文件夹的方式、等宽字体、
#   ffmpeg 搜索路径、高 DPI 设置）都集中在本区块，其它代码一律调用这里的
#   函数，不要在各处零散地写 sys.platform 判断。
#   新增平台相关逻辑时，请同时补齐三个分支，保持行为一致。
# ============================================================================

PLATFORM_WINDOWS = "windows"
PLATFORM_MACOS = "macos"
PLATFORM_LINUX = "linux"

APP_NAME = "video_frame_tool"          # 配置目录名（用户级）


def detect_platform(platform_string=None):
    """
    按平台字符串判定操作系统类型，返回 PLATFORM_* 之一。

    :param platform_string: 平台标识（如 'win32' / 'darwin' / 'linux'）；
                            留空时取 sys.platform。显式传参是为了让单元测试
                            与工具脚本能够校验各平台分支，而不必真的换系统。

    注意：这里判断的是"运行时的操作系统类型"，不是"当前这台电脑"的型号，
    因此同一份代码在三大平台上走各自的分支，不做任何硬编码假设。
    """
    p = sys.platform if platform_string is None else platform_string
    if p.startswith("win"):
        return PLATFORM_WINDOWS
    if p == "darwin":
        return PLATFORM_MACOS
    return PLATFORM_LINUX              # linux / 其它类 Unix 系统


PLATFORM = detect_platform()
IS_WINDOWS = PLATFORM == PLATFORM_WINDOWS
IS_MACOS = PLATFORM == PLATFORM_MACOS
IS_LINUX = PLATFORM == PLATFORM_LINUX


def user_config_dir(platform=None, create=True):
    """
    返回用户级配置目录（各平台遵循各自的惯例，不硬编码某平台路径）：

      Windows : %APPDATA%\\video_frame_tool        （通常 C:\\Users\\<用户>\\AppData\\Roaming）
      macOS   : ~/Library/Application Support/video_frame_tool
      Linux   : $XDG_CONFIG_HOME/video_frame_tool  或  ~/.config/video_frame_tool

    :param platform: 指定平台（默认取当前运行平台）
    :param create:   是否自动创建目录；创建失败回退到当前工作目录
    """
    platform = platform or PLATFORM
    if platform == PLATFORM_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser(os.path.join("~", "AppData", "Roaming"))
    elif platform == PLATFORM_MACOS:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_NAME)
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            return os.getcwd()
    return path


def config_file_path(platform=None):
    """配置文件绝对路径（JSON）"""
    return os.path.join(user_config_dir(platform), "settings.json")


def open_folder(path, platform=None):
    """
    用系统自带的文件管理器打开一个目录（各平台调用各自的命令）：

      Windows : os.startfile（等价于资源管理器打开）
      macOS   : open
      Linux   : xdg-open（绝大多数桌面发行版都自带）

    返回是否成功。
    """
    if not os.path.isdir(path):
        return False
    platform = platform or PLATFORM
    try:
        if platform == PLATFORM_WINDOWS:
            os.startfile(path)                       # 仅 Windows 存在的 API
        elif platform == PLATFORM_MACOS:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def mono_font_family(platform=None):
    """各平台自带的等宽字体（用于日志框），保证日志对齐不错位"""
    return {
        PLATFORM_WINDOWS: "Consolas",
        PLATFORM_MACOS: "Menlo",
        PLATFORM_LINUX: "DejaVu Sans Mono",
    }.get(platform or PLATFORM, "Courier New")


def ffmpeg_search_dirs(platform=None):
    """
    除 PATH 之外，各平台常见的 ffmpeg 安装目录。
    返回目录列表，调用方逐个拼接可执行文件名判断是否存在。

    说明：Windows 分支用 ntpath.join 拼路径，保证无论在哪台机器上生成，
          得到的都是规范的 Windows 路径（反斜杠），便于日志排查。
    """
    platform = platform or PLATFORM
    if platform == PLATFORM_WINDOWS:
        j = ntpath.join
        home = os.path.expanduser("~")
        return [
            j(os.environ.get("ProgramFiles", r"C:\Program Files"), "ffmpeg", "bin"),
            r"C:\ffmpeg\bin",
            j(home, "scoop", "shims"),
            j(home, "AppData", "Local", "Microsoft", "WinGet", "Links"),
            j(home, "AppData", "Local", "Programs", "ffmpeg", "bin"),
        ]
    if platform == PLATFORM_MACOS:
        return ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]
    return ["/usr/bin", "/usr/local/bin", "/snap/bin", "/var/lib/flatpak/exports/bin"]


def exe_name(name, platform=None):
    """可执行文件名（Windows 需要 .exe 后缀，其余平台原样）"""
    return name + ".exe" if (platform or PLATFORM) == PLATFORM_WINDOWS else name


def enable_high_dpi():
    """
    高 DPI 适配（仅 Windows 需要）。
    Windows 默认按 96 DPI 缩放，高分屏下 Tk 界面会发虚，这里开启 DPI 感知。
    macOS / Linux 由系统或窗口管理器处理，无需额外设置。
    """
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


# ============================================================================
# 一、默认参数与常量
# ============================================================================
# 说明：这里不预设任何素材路径。程序会把用户"上次选择过的路径"记录在
#       settings.json 里（位置见"零、平台适配层"的 user_config_dir），
#       下次启动自动回填；首次运行需要用户手动选择一次。
# ============================================================================

# ---- 产品图叠加 ----
MARGIN_BOTTOM = 20            # 产品图距视频底部的像素距离
PRODUCT_WIDTH_RATIO = 0.25    # 产品图宽度 = 视频宽度 * 该比例
PRODUCT_START_FRAME = 60      # 产品图从第几帧开始显示（默认第 60 帧；填 0 表示首帧就显示）
PRODUCT_START_MAX = 100000    # 起始帧输入框的防御性上限
# 商品图显示概率（%）：每条视频**独立**掷一次骰子，不是"整批抽 N 条"。
#   0   → 完全不叠加商品图（这条只剩首图 + 画中画）
#   100 → 每条视频都从主图目录里随机取一张叠加
#   40  → 40% 的概率叠加，60% 的概率这条不叠
# 概率为 0 或掷骰未命中时，是**真的不做这道工序**（省掉一路输入和一段 overlay），
# 而不是叠一张透明图糊弄过去（见 _decide_product / build_command）。
PRODUCT_CHANCE = 100

# ---- 画中画默认几何（比例均相对主视频宽/高，对应参考截图红框） ----
PIP_W_RATIO = 0.24            # 宽 24%
PIP_H_RATIO = 0.20            # 高 20%
PIP_RIGHT_RATIO = 0.08        # 右边缘距视频右侧 8%
PIP_TOP_RATIO = 0.09          # 上边缘距视频顶部 9%
PIP_FILL = "裁剪填满"          # 内容填充方式：裁剪填满 / 完整显示

# ---- 画中画素材处理默认值 ----
PIP_HEAD_TRIM = 10.0          # 默认掐掉素材前 10% 的时长
PIP_TAIL_TRIM = 10.0          # 默认掐掉素材后 10% 的时长
PIP_SPEED = 1.2               # 默认加速倍数
# 单个片段的随机时长范围（秒）。
# 性能相关：段数 ≈ 主视频时长 / 平均段长，段数直接决定画中画阶段的进程数与总耗时。
# 3~8 秒会产生过多片段（9 分钟视频约 100 段），故放宽到 4~12 秒（段数约减半）。
SEG_MIN, SEG_MAX = 4.0, 12.0
MAX_SEGMENTS = 600            # 单个成片最多拼接的片段数（防御性上限，防死循环）

# ---- 画中画编码的批处理参数（性能关键，改动前请看"性能设计"） ----
# 早期实现是"一段一个 ffmpeg 进程、串行执行"，9 分钟视频要启动 100+ 次进程；
# 现在改为把 PIP_BATCH 个片段塞进**同一个 ffmpeg 进程**（多输入 + concat 滤镜）一次编完，
# 进程数从 100+ 降到个位数，且批次之间并行执行。
PIP_BATCH = 8                 # 每批片段数（= 单个 ffmpeg 进程的输入路数）
PIP_BATCH_PRESET = "ultrafast"  # 批次编码档位：画面小（约 260x384）+ 后面还会被重编码一次
PIP_BATCH_THREADS = 2         # 单批次编码线程数（批内已有多路输入并行解码，不宜再吃多线程）

# ---- 画中画素材的"低清副本"（实测最大的提速项，改动前请看 README 的性能实测） ----
# 画中画在成片里最终只占约 260x384 像素，而素材原片常见 1080p/4K。
# 逐项消融实测（48 段 / 3 并发）：
#     现状                       13~14 秒，内存峰值 6.2 GB
#     加硬件解码                  8.1 秒（-42%）
#     素材先降成小副本再拼接       1.0 秒（-93%），内存峰值 0.84 GB（-86%）
# 原因：图像滤镜、调色、翻转、加速、甚至编码本身的开销都约等于 0，
#       全部时间都花在"解码 1080p 素材"上。把素材预先变小，解码量直接砍掉一个数量级，
#       而观感毫无差别——因为素材最终也只显示那么大。
# 副本**只生成一次，之后永久保留**（不会在任务结束后被清理）：
# 每次开始处理前先按台账核对一遍，已经生成过的直接拿来用，一个字节都不重做。
# 副本目录放在素材目录内，不会被当成素材扫进来（_list_videos 不递归）。
PIP_SMALL_DIR = ".pip_small"    # 副本存放目录名（位于素材目录内）
PIP_SMALL_THREADS = 4           # 并发生成副本的线程数（6 个并发只快 10%，但内存多 1.5 倍）
PIP_SMALL_CRF = 20              # 副本画质（20 接近视觉无损，且副本后面还会被重编码）
PIP_SMALL_GOP = 15              # 副本关键帧间隔（越小定位越准、越快）
PIP_SMALL_MIN_SHORT = 480       # 副本短边下限（保证够画中画目标尺寸用）
PIP_SMALL_HEADROOM = 1.15       # 相对画中画目标尺寸的额外安全余量
PIP_SMALL_PRESET = "veryfast"   # 副本编码档位（副本后面还要被重编码一次，不必更慢的档）
# 副本"规格版本"：任何会改变副本产出的改动（缩放策略、编码参数、尺寸算法）都要 +1，
# 否则用户会一直用着旧规格的副本。+1 后旧副本会在下次处理时自动重做。
PIP_SMALL_V = 1
PIP_SMALL_META = "_meta.json"   # 副本台账：记录每个副本对应哪个源文件、按什么参数生成
PIP_SMALL_ROSTER = "_说明.txt"  # 放进副本目录的说明（告诉用户这些文件是什么、能不能删）

# ---- 抗查重随机化默认值 ----
RND_FLIP_H = True             # 随机水平翻转（默认开）
RND_ZOOM = 1.06               # 随机缩放上限（1.0 = 关闭）
RND_COLOR = True              # 随机亮度/对比度/饱和度微扰
RND_JITTER = 3                # 画中画位置随机抖动像素（0 = 关闭）

# ---- 编码参数（preset 与 CRF 是一组，改之前先看实测表） ----
# 成片的耗时几乎全在编码：实测"只解码不编码"0.34s，而完整命令 6.7s —— 滤镜、overlay、
# 缩放、素材解码全在噪声里（见 README「性能实测」）。所以**唯一有效的提速手段是降低
# 编码器工作量**，而 preset 就是那个总开关。
# 实测（1080p/20s 竖屏，同一条滤镜链，只换编码档位，3 轮取中位；5 并发下同向）：
#     veryfast  /CRF18   1.00x   27.8MB   SSIM 1.0000   ← 旧默认
#     superfast /CRF18   1.43x   37.2MB   SSIM 0.9889
#     superfast /CRF20   1.57x   28.6MB   SSIM 0.9870   ← 现默认（提速且体积持平）
#     superfast /CRF21   1.65x   25.0MB   SSIM 0.9858
#     ultrafast /CRF24   2.59x   51.0MB   SSIM 0.9678   ← 体积近两倍，不采用
#     fast      /CRF18   0.35x   26.1MB   SSIM 0.9909   ← 比 veryfast 慢 2.9 倍
# 结论：preset 每降一档收益约 40%，而 CRF 同时 +2 可把体积找回来 —— 所以默认档位
# 定为 superfast/CRF20。注意**不要为了提速换硬件编码**：苹果媒体引擎是共享资源，
# 单条 2.27x，5 并发只剩 1.06x（详见 README「硬件加速实测」）。
CRF = 20                      # x264 质量；与 preset 配套（20 配 superfast，体积≈旧的 veryfast/18）
PRESET_MAP = {                # 界面下拉 → ffmpeg preset
    # 旧配置里存的"快速/均衡/高质量"三个名字保持不变，映射整体上移一档：
    # 老用户重新打开就自动变快，不需要迁移配置。
    "快速": "superfast",      # 默认档，实测 1.57x
    "均衡": "veryfast",       # 原为 fast（实测只有 0.35x，名不副实）
    "高质量": "fast",         # 原为 medium
    "最高质量": "medium",      # 压得最狠，慢，仅在需要极限压缩率时用
}
DEFAULT_PRESET = "快速"
DEFAULT_WORKERS = 5           # 默认并发线程数
MONITOR_INTERVAL_MS = 1000    # 系统资源监控的刷新间隔（毫秒）

# ---- 硬件加速（界面「硬件加速」下拉）----
# 只加速"解码"。为什么不给硬件编码：实测硬编更慢（见 hw_decode_args 里的数据），
# 因为这条流水线里编码几乎不花时间，换硬编只会多出把画面搬进搬出 GPU 的开销。
HWACCEL_AUTO = "自动"          # macOS 自动开硬解；其它平台保持现状（软件解码）
HWACCEL_ON = "开启"            # 强制启用硬解（非 macOS 交给 ffmpeg 自选 cuda/qsv/vaapi）
HWACCEL_OFF = "关闭"           # 一律软件解码（排查花屏 / 兼容性问题时用）
HWACCEL_MODES = (HWACCEL_AUTO, HWACCEL_ON, HWACCEL_OFF)
DEFAULT_HWACCEL = HWACCEL_AUTO
# 硬解用在哪几个阶段：只有"要解码 1080p/4K 原片"的阶段才真快，
# 其余阶段实测没收益甚至更慢，所以"自动"只在这些阶段开（见 hw_decode_args 实测表）
HW_STAGE_COPY = "copy"          # 生成低清副本：解码 1080p/4K 原片
HW_STAGE_PIP_RAW = "pip_raw"    # 画中画片段编码，输入是原片
HW_STAGE_PIP_SMALL = "pip_small"  # 画中画片段编码，输入是 480p 副本
HW_STAGE_MAIN = "main"          # 主合成：解码主视频
HW_AUTO_STAGES = (HW_STAGE_COPY, HW_STAGE_PIP_RAW)

# ---- 其它 ----
CPU_COUNT = os.cpu_count() or 4
LOG_MAX_LINES = 20          # 日志控件保留的最大行数，超出丢弃最老的
SCAN_THREADS = 16             # 素材扫描的并行探测线程数

# ---- 配置文件结构（默认值的唯一来源：上面的常量） ----
SETTINGS_VERSION = 1
DEFAULT_SETTINGS = {
    "version": SETTINGS_VERSION,
    "paths": {                          # 上次选择过的路径（核心：代码里不写死任何路径）
        "cover": "",                    # 首图目录（每条视频随机取一张；也兼容单个图片文件）
        "product": "",                  # 主图目录（同上）
        "video_dir": "",                # 待处理视频目录
        "pip_dir": "",                  # 画中画目录
    },
    "pip": {                            # 画中画参数（界面输入框用字符串保存）
        "enabled": True,                # 是否启用画中画（总开关；关掉后不看下面的参数）
        "w": str(int(PIP_W_RATIO * 100)),
        "h": str(int(PIP_H_RATIO * 100)),
        "right": str(int(PIP_RIGHT_RATIO * 100)),
        "top": str(int(PIP_TOP_RATIO * 100)),
        "fill": PIP_FILL,
        "head": f"{PIP_HEAD_TRIM:g}",
        "tail": f"{PIP_TAIL_TRIM:g}",
        "speed": f"{PIP_SPEED:g}",
        "small": True,                  # 是否为画中画素材建立低清加速副本（强烈建议开）
    },
    "random": {                         # 抗查重随机化开关
        "flip_h": RND_FLIP_H,
        "zoom": f"{RND_ZOOM:g}", "color": RND_COLOR, "jitter": str(RND_JITTER),
    },
    "run": {                            # 运行参数
        "workers": DEFAULT_WORKERS, "preset": DEFAULT_PRESET,
        "prod_start": PRODUCT_START_FRAME,   # 产品图起始帧（默认 60）
        "prod_chance": PRODUCT_CHANCE,       # 商品图显示概率（%，每条视频独立掷骰）
        "hwaccel": DEFAULT_HWACCEL,          # 硬件加速：自动 / 开启 / 关闭（见 hw_decode_args）
        "include_first": False,              # 旧字段，仅用于兼容老配置，见 load_settings
    },
}

VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".flv", ".wmv",
    ".webm", ".ts", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".rmvb",
}
KEEP_EXT = {".mp4", ".mov", ".m4v", ".mkv"}   # 可直接承载 h264 的容器，其余统一输出 .mp4

# 可当首图 / 主图用的图片后缀。
# 首图和主图都支持「给一个目录，每条视频从里面随机取一张」。
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".jfif"}


# ============================================================================
# 一·五、用户配置读写（"记住上次选择"的核心实现）
# ============================================================================
# 配置文件位置由 user_config_dir() 按平台决定，内容为 JSON：
#   paths   —— 首图目录 / 主图目录 / 视频目录 / 画中画目录（上次选择的路径）
#   pip     —— 画中画几何、掐头去尾、加速
#   random  —— 抗查重随机化开关
#   run     —— 并发数、编码档位、首帧叠加
# 任何路径都不写死在代码里，全部由用户选择后记录、下次启动回填。
# ============================================================================

def _deep_merge(base, override):
    """
    递归合并字典：override 里有的键覆盖 base，没有的沿用 base。
    用于兼容"旧版本配置文件缺少新字段"的情况。
    """
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings():
    """
    读取用户配置；文件不存在或内容损坏时返回默认配置。
    返回值结构与 DEFAULT_SETTINGS 完全一致（缺失字段自动补默认）。

    兼容性处理：老版本用 run.include_first（布尔：是否在首帧也叠加产品图），
    新版本改为 run.prod_start（从第几帧开始叠加）。读取到老配置时换算过去，
    这样用户的旧选择不会丢。
    """
    default = json.loads(json.dumps(DEFAULT_SETTINGS))     # 深拷贝一份默认配置
    try:
        with open(config_file_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return default

    merged = _deep_merge(default, data)
    try:
        raw_run = data.get("run", {}) if isinstance(data, dict) else {}
        if "prod_start" not in raw_run and raw_run.get("include_first"):
            merged["run"]["prod_start"] = 0                # 老配置勾了"首帧也叠加" → 从第 0 帧起
    except Exception:
        pass
    return merged


def save_settings(data):
    """
    原子写入用户配置：先写 .tmp 再 os.replace，避免写一半崩溃导致配置损坏。
    保存失败不影响主流程（返回 False 即可，不抛异常）。
    """
    path = config_file_path()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ============================================================================
# 一·六、系统资源监控（CPU / 内存 / 本进程占用）
# ============================================================================
# 界面底部实时显示 CPU 与内存占用。实现原则：
#   1. **不引入第三方依赖**（不用 psutil）：Linux 读 procfs，
#      macOS 用 ctypes 调 mach 内核接口，Windows 调 Win32 API；
#   2. 采样必须便宜且不阻塞界面：上述接口都是微秒级，每秒采样一次；
#   3. 任何一步失败都只影响这一项显示（值为 None → 界面显示 "—"），绝不抛异常。
#
# 名词说明：
#   cpu       —— 全系统 CPU 使用率 0~100%（所有核心合计的忙碌比例），需要两次采样算差值
#   mem_used  —— 系统已用内存，口径对齐 macOS「活动监视器」的"已用内存"
#                = (匿名页 - 可回收页) + 常驻页(wired) + 压缩页
#   self_rss  —— 本进程常驻内存（RSS）
# ============================================================================

def fmt_bytes(n):
    """把字节数格式化成人类可读字符串；None → "—"（表示该平台取不到）"""
    if n is None:
        return "—"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


def fmt_duration(seconds):
    """把秒数格式化成"1 分 25 秒"这类便于阅读的形式（日志是给普通用户看的）"""
    try:
        s = max(0, int(round(float(seconds))))
    except Exception:
        return "—"
    if s < 60:
        return f"{s} 秒"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} 分 {s} 秒"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"


class _LinuxResReader:
    """Linux：读 procfs。内核直接导出，无需权限，不存在失败风险。"""

    platform = PLATFORM_LINUX
    name = "procfs"

    def __init__(self):
        self.page = os.sysconf("SC_PAGE_SIZE")
        with open("/proc/stat", "r", encoding="ascii", errors="ignore") as fh:
            fh.readline()                    # 自检：文件必须可读

    def cpu_ticks(self):
        """返回 (忙碌 tick, 总 tick)，单位是内核时钟节拍"""
        try:
            with open("/proc/stat", "r", encoding="ascii", errors="ignore") as fh:
                vals = [int(x) for x in fh.readline().split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
            return sum(vals) - idle, sum(vals)
        except Exception:
            return None

    def memory(self):
        """返回 (已用字节, 总字节)；优先用 MemAvailable（比 MemFree 更接近真实可用）"""
        try:
            info = {}
            with open("/proc/meminfo", "r", encoding="ascii", errors="ignore") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    info[key.strip()] = int(rest.split()[0]) * 1024
            total = info.get("MemTotal")
            avail = info.get("MemAvailable", info.get("MemFree"))
            if not total or avail is None:
                return None
            return total - avail, total
        except Exception:
            return None

    def self_rss(self):
        """读 /proc/self/statm 第 2 个字段（常驻页数）"""
        try:
            with open("/proc/self/statm", "r", encoding="ascii") as fh:
                return int(fh.read().split()[1]) * self.page
        except Exception:
            return None


class _MacResReader:
    """
    macOS：ctypes 调 mach 内核接口（等价于活动监视器/ vm_stat 的数据源）。

      CPU   host_processor_info(PROCESSOR_CPU_LOAD_INFO) → 每核 user/system/idle/nice 累计 tick
      内存  host_statistics64(HOST_VM_INFO64)            → vm_statistics64（页数）
      自身  task_info(MACH_TASK_BASIC_INFO)              → resident_size
    """

    platform = PLATFORM_MACOS
    name = "mach API"

    # mach 常量
    _PROCESSOR_CPU_LOAD_INFO = 2
    _HOST_VM_INFO64 = 4
    _MACH_TASK_BASIC_INFO = 20

    class _VmStat64(ctypes.Structure):
        """对应 vm_statistics64_data_t（字段顺序必须与内核头文件一致）"""
        _fields_ = [
            ("free_count", ctypes.c_uint32), ("active_count", ctypes.c_uint32),
            ("inactive_count", ctypes.c_uint32), ("wire_count", ctypes.c_uint32),
            ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
            ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64),
            ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
            ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64),
            ("purges", ctypes.c_uint64), ("purgeable_count", ctypes.c_uint32),
            ("speculative_count", ctypes.c_uint32), ("decompressions", ctypes.c_uint64),
            ("compressions", ctypes.c_uint64), ("swapins", ctypes.c_uint64),
            ("swapouts", ctypes.c_uint64), ("compressor_page_count", ctypes.c_uint32),
            ("throttled_count", ctypes.c_uint32), ("external_page_count", ctypes.c_uint32),
            ("internal_page_count", ctypes.c_uint32),
            ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
        ]

    class _TaskBasicInfo(ctypes.Structure):
        """对应 mach_task_basic_info_data_t"""
        _fields_ = [
            ("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
            ("resident_size_max", ctypes.c_uint64),
            ("user_time", ctypes.c_int32 * 2), ("system_time", ctypes.c_int32 * 2),
            ("policy", ctypes.c_int32), ("suspend_count", ctypes.c_int32),
        ]

    def __init__(self):
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libc.mach_host_self.restype = ctypes.c_uint32
        libc.mach_task_self.restype = ctypes.c_uint32

        # host_processor_info(host, flavor, &cpu_count, &info_array, &info_count)
        libc.host_processor_info.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_uint32)]
        libc.host_processor_info.restype = ctypes.c_int
        # host_statistics64(host, flavor, &info, &info_count)
        libc.host_statistics64.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(self._VmStat64),
            ctypes.POINTER(ctypes.c_uint32)]
        libc.host_statistics64.restype = ctypes.c_int
        # task_info(task, flavor, &info, &info_count)
        libc.task_info.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(self._TaskBasicInfo),
            ctypes.POINTER(ctypes.c_uint32)]
        libc.task_info.restype = ctypes.c_int
        # vm_deallocate(task, address, size) —— 释放内核返回的 CPU tick 数组，防内存泄漏
        libc.vm_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        libc.vm_deallocate.restype = ctypes.c_int

        self.libc = libc
        self.host = libc.mach_host_self()
        self.task = libc.mach_task_self()
        self.page = os.sysconf("SC_PAGE_SIZE")
        self.total = os.sysconf("SC_PHYS_PAGES") * self.page

        # 自检：拿不到数据说明该平台/该 Python 不支持，交给上层回退
        if self.cpu_ticks() is None or self.memory() is None:
            raise RuntimeError("mach API 不可用")

    def cpu_ticks(self):
        """所有核心的 (忙碌 tick, 总 tick) 之和"""
        try:
            count = ctypes.c_uint32(0)
            info = ctypes.POINTER(ctypes.c_int)()
            info_cnt = ctypes.c_uint32(0)
            kr = self.libc.host_processor_info(
                self.host, self._PROCESSOR_CPU_LOAD_INFO,
                ctypes.byref(count), ctypes.byref(info), ctypes.byref(info_cnt))
            if kr != 0 or not info_cnt.value:
                return None
            vals = [info[i] for i in range(info_cnt.value)]
            addr = ctypes.cast(info, ctypes.c_void_p).value
            self.libc.vm_deallocate(self.task, ctypes.c_void_p(addr),
                                    ctypes.c_uint32(info_cnt.value * ctypes.sizeof(ctypes.c_int)))
            busy = total = 0
            for i in range(count.value):
                user, system, idle, nice = vals[i * 4:i * 4 + 4]
                busy += user + system + nice
                total += user + system + nice + idle
            return busy, total
        except Exception:
            return None

    def memory(self):
        """已用内存口径对齐「活动监视器」：匿名页-可回收 + wired + 压缩页"""
        try:
            st = self._VmStat64()
            cnt = ctypes.c_uint32(ctypes.sizeof(self._VmStat64) // ctypes.sizeof(ctypes.c_int))
            if self.libc.host_statistics64(self.host, self._HOST_VM_INFO64,
                                           ctypes.byref(st), ctypes.byref(cnt)) != 0:
                return None
            pages = (st.internal_page_count - st.purgeable_count
                     + st.wire_count + st.compressor_page_count)
            return pages * self.page, self.total
        except Exception:
            return None

    def self_rss(self):
        try:
            ti = self._TaskBasicInfo()
            cnt = ctypes.c_uint32(ctypes.sizeof(self._TaskBasicInfo) // ctypes.sizeof(ctypes.c_int))
            if self.libc.task_info(self.task, self._MACH_TASK_BASIC_INFO,
                                   ctypes.byref(ti), ctypes.byref(cnt)) != 0:
                return None
            return int(ti.resident_size)
        except Exception:
            return None


class _WindowsResReader:
    """Windows：kernel32 取 CPU 与内存，psapi 取本进程常驻内存"""

    platform = PLATFORM_WINDOWS
    name = "Win32 API"

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def __init__(self):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self.curr = self.k32.GetCurrentProcess()
        if self.memory() is None:
            raise RuntimeError("Win32 内存接口不可用")

    def cpu_ticks(self):
        """GetSystemTimes 返回的都是累计值（内核时间已含空闲时间，故忙碌 = 内核+用户-空闲）"""
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                            ("dwHighDateTime", ctypes.c_uint32)]

            idle, kern, user = FILETIME(), FILETIME(), FILETIME()
            if not self.k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                                           ctypes.byref(user)):
                return None

            def val(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            i, k, u = val(idle), val(kern), val(user)
            return (k + u - i), (k + u)
        except Exception:
            return None

    def memory(self):
        try:
            st = self._MemoryStatusEx()
            st.dwLength = ctypes.sizeof(self._MemoryStatusEx)
            if not self.k32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullTotalPhys - st.ullAvailPhys, st.ullTotalPhys
        except Exception:
            return None

    def self_rss(self):
        """本进程常驻内存（WorkingSetSize）"""
        try:
            pmc = self._ProcessMemoryCounters()
            pmc.cb = ctypes.sizeof(self._ProcessMemoryCounters)
            if not self.psapi.GetProcessMemoryInfo(self.curr, ctypes.byref(pmc), pmc.cb):
                return None
            return int(pmc.WorkingSetSize)
        except Exception:
            return None


class _FallbackResReader:
    """
    兜底：拿不到系统接口时使用。
    CPU 与内存均不提供，界面显示缺失标记。
    """

    platform = None
    name = "无可用系统接口"

    def cpu_ticks(self):
        return None

    def memory(self):
        return None

    def self_rss(self):
        return None


class SystemMonitor:
    """
    系统资源采样器（界面每秒调用一次）。

    平台实现由各 Reader 的 `platform` 字段决定；构造失败或采样失败都只降级显示，
    不影响主流程。CPU 使用率需要两次采样算差值，因此第一秒会显示 "—"。
    """

    def __init__(self):
        self._reader = None
        for cls in (_MacResReader, _WindowsResReader, _LinuxResReader):
            if cls.platform != PLATFORM:
                continue                     # 只实例化当前操作系统的实现
            try:
                self._reader = cls()
            except Exception:
                self._reader = None
        if self._reader is None:
            self._reader = _FallbackResReader()

        self.source = self._reader.name      # 界面上标明数据来源，便于排查
        self._prev = None                    # 上一次 CPU tick 快照

    def sample(self):
        """
        采样一次，返回 {"cpu", "mem_used", "mem_total", "self_rss"}。
        取不到的项为 None（界面显示 "—"）；本函数保证不抛异常。
        """
        out = {"cpu": None, "mem_used": None, "mem_total": None, "self_rss": None}

        try:
            cur = self._reader.cpu_ticks()
            if cur and self._prev and cur[1] > self._prev[1]:
                delta_busy = cur[0] - self._prev[0]
                delta_all = cur[1] - self._prev[1]
                out["cpu"] = max(0.0, min(100.0, 100.0 * delta_busy / delta_all))
            self._prev = cur                  # 首次采样只记快照，下一次才有差值
        except Exception:
            self._prev = None

        try:
            mem = self._reader.memory()
            if mem:
                out["mem_used"], out["mem_total"] = mem
        except Exception:
            pass

        try:
            out["self_rss"] = self._reader.self_rss()
        except Exception:
            pass

        return out


# ============================================================================
# 一·七、程序图标（内嵌 logo）
# ============================================================================
# 图标直接以 base64 PNG 内嵌在源码里，**不需要任何外部图片文件**，
# 拷走单个 .py 就能在三大平台正常显示窗口图标，也不用装 Pillow 之类的库。
#
# 想换成自己的品牌 logo：把一个 png 图片命名为 logo.png，
# 放到（任选其一，优先脚本同目录）：
#     1) video_frame_tool.py 所在目录
#     2) 用户配置目录（位置见"零、平台适配层"的 user_config_dir）
# 程序启动时优先用你的图片，找不到才用内嵌图标。建议正方形、256x256 以上。
#
# ⚠ 为什么下面要自己解 PNG（实测踩过的坑，别删）：
#   macOS 自带的 /usr/bin/python3 绑的是 **Tk 8.5**，它既不认 PNG
#   （`PhotoImage(data=png_base64)` → "couldn't recognize image data"），
#   也不认 base64 形式的 PPM。Tk 8.5 唯一能读的位图格式是**PPM 文件**。
#   于是这里用纯标准库把内嵌 PNG 解出来、缩到目标尺寸、落盘成 PPM 再加载。
#   Tk 8.6+ 走正常 PNG 路径，只在 8.5 上才触发这条回退。
# ============================================================================

LOGO_FILE_NAME = "logo.png"          # 自定义 logo 的文件名
_LOGO_PPM_FILES = {}                 # size -> 已经转好的 PPM 临时文件路径
_LOGO_EMBEDDED_CACHE = []            # 内嵌 PNG 解码后的字节（延迟解，避免 import 期开销）
LOGO_PNG_B64 = (
    "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"
    "RK5CYII="
)


def _logo_embedded_png():
    """内嵌 logo 的 PNG 原始字节（解一次就缓存）。"""
    import base64 as _b64
    if not _LOGO_EMBEDDED_CACHE:
        _LOGO_EMBEDDED_CACHE.append(_b64.b64decode(LOGO_PNG_B64))
    return _LOGO_EMBEDDED_CACHE[0]


def _png_decode(png_bytes):
    """
    纯标准库解 8 位 PNG，返回 (宽, 高, 通道数, 像素字节)。

    只服务内嵌 logo：macOS 自带 python3 的 Tk 是 8.5，读不了 PNG，必须自己解。
    支持颜色类型 0/2/4/6（灰度 / RGB / 灰度+A / RGBA）与全部 5 种行滤波方式。
    调色板（类型 3）与 16 位深不支持——内嵌图是我们自己生成的，不会用到。
    """
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 数据")
    pos, idat, w, h, ct = 8, bytearray(), 0, 0, 0
    while pos + 8 <= len(png_bytes):
        (ln,) = struct.unpack(">I", png_bytes[pos:pos + 4])
        typ = png_bytes[pos + 4:pos + 8]
        body = png_bytes[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", body[:10])
            if bd != 8:
                raise ValueError("只支持 8 位 PNG")
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    ch = {0: 1, 2: 3, 4: 2, 6: 4}.get(ct)
    if not ch:
        raise ValueError("不支持的颜色类型 %s" % ct)

    raw = zlib.decompress(bytes(idat))
    stride, p = w * ch, 0
    prev = bytearray(stride)
    out = bytearray()
    for _ in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:                                   # Sub
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:                                 # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:                                 # Average
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:                                 # Paeth
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                c = prev[i - ch] if i >= ch else 0
                b = prev[i]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif f != 0:
            raise ValueError("不支持的行滤波 %d" % f)
        out += line
        prev = line
    return w, h, ch, bytes(out)


def _png_to_ppm_file(png_bytes, size):
    """
    把 PNG 缩小到 size×size 并落盘为 PPM（P6），返回文件路径。

    Tk 8.5 只认 PPM 文件，这是老 Tk 上唯一能让 logo 显示出来的路子。
    缩放用最近邻抽样（图标是几何色块，效果足够，且零依赖）。
    """
    w, h, ch, px = _png_decode(png_bytes)
    if size > w:                                     # 目标比原图大就不放大，避免无谓糊图
        size = w
    step = max(1, w // size)
    vstep = max(1, h // size)
    rows = []
    for y in range(0, h, vstep):
        row = bytearray()
        base = y * w
        for x in range(0, w, step):
            o = (base + x) * ch
            if ch >= 3:
                row += px[o:o + 3]
            else:
                row += bytes((px[o],)) * 3
        rows.append(bytes(row))
    rows = rows[:size]

    path = os.path.join(tempfile.gettempdir(), "video_frame_tool_logo_%d.ppm" % size)
    with open(path, "wb") as fh:
        fh.write(b"P6\n%d %d\n255\n" % (size, len(rows)))
        for r in rows:
            fh.write(r)
    return path


def _logo_search_paths():
    """自定义 logo 的查找位置（按优先级）"""
    return [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), LOGO_FILE_NAME),
        os.path.join(user_config_dir(), LOGO_FILE_NAME),
    ]


def _logo_shrink(img, size):
    """Tk 只能整数倍缩图，取最接近的倍数把 img 缩到约 size 像素。"""
    factor = max(1, int(round(max(img.width(), img.height()) / float(size))))
    return img.subsample(factor, factor) if factor > 1 else img


def _logo_load_via_ppm(png_bytes, size):
    """
    老 Tk（8.5）回退路径：PNG → PPM 临时文件 → PhotoImage。
    转好的 PPM 按尺寸缓存，避免窗口图标与标题图标各转一次。
    """
    path = _LOGO_PPM_FILES.get(size)
    if not path or not os.path.isfile(path):
        path = _png_to_ppm_file(png_bytes, size)
        _LOGO_PPM_FILES[size] = path
    return tk.PhotoImage(file=path)


def load_logo(size=64):
    """
    载入 logo 图片，返回 (PhotoImage 或 None, 来源说明)。

    优先级：脚本同目录的 logo.png → 配置目录的 logo.png → 内嵌图标。
    每一条都先试 Tk 原生 PNG 加载，失败再走 PPM 回退（Tk 8.5 场景）。
    必须在创建 Tk 根窗口之后调用（PhotoImage 依赖 Tk 环境）。
    任何失败都只返回 (None, 原因)，不抛异常、不影响主流程。
    """
    # ---- 1) 用户自定义图片 ----
    for path in _logo_search_paths():
        if not os.path.isfile(path):
            continue
        try:
            return _logo_shrink(tk.PhotoImage(file=path), size), path
        except Exception:
            pass
        try:                                          # 老 Tk 读不了 PNG 文件 → 自己转 PPM
            with open(path, "rb") as fh:
                return _logo_load_via_ppm(fh.read(), size), path + "（PPM 回退）"
        except Exception:
            continue                                  # 用户图片读不了就继续看下一个

    # ---- 2) 内嵌图标：Tk 8.6+ 直接吃 base64 PNG ----
    try:
        return _logo_shrink(tk.PhotoImage(data=LOGO_PNG_B64), size), "内嵌图标"
    except Exception:
        pass

    # ---- 3) 内嵌图标：PPM 回退（Tk 8.5）----
    try:
        return _logo_load_via_ppm(_logo_embedded_png(), size), "内嵌图标（PPM 回退）"
    except Exception as e:
        return None, "内嵌图标不可用（%s）" % e


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


# ============================================================================
# 四、进程管理（可中断的 ffmpeg 调用）
# ============================================================================

class ProcRegistry:
    """
    记录当前正在运行的 ffmpeg 子进程，供"停止"按钮统一终止。
    线程安全：worker 线程会并发注册/注销。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._procs = set()

    def add(self, p):
        with self._lock:
            self._procs.add(p)

    def discard(self, p):
        with self._lock:
            self._procs.discard(p)

    def count(self):
        """当前在跑的 ffmpeg 数量（界面监控栏用来显示"正在处理 N 个"）"""
        with self._lock:
            return len(self._procs)

    def kill_all(self):
        """终止所有在跑的 ffmpeg（停止按钮调用）"""
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


# ffmpeg -progress 每秒左右吐一批 key=value，这里只认 out_time（已编码时长）。
# 不用 out_time_us / out_time_ms：这两个字段的单位在不同 ffmpeg 版本里不一致
# （历史上 out_time_ms 实际是微秒），踩过一次就不碰了。
_PROGRESS_TIME_RE = re.compile(rb"^out_time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def _read_out_time(line, expected_dur):
    """
    把 ffmpeg -progress 的一行折成"真实完成度"（0~1）；看不懂的行返回 None。

    这是整条流水线里**唯一**的真实进度来源：ffmpeg 自己编码了多少秒，
    再除以这条成片的时长，就是这条视频此刻真正的完成度。
    """
    if line.startswith(b"progress="):
        return 1.0 if line.strip() == b"progress=end" else None
    m = _PROGRESS_TIME_RE.match(line)
    if not m or expected_dur <= 0:
        return None
    secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return min(1.0, secs / expected_dur)


def _run(cmd, registry, on_progress=None, expected_dur=0.0):
    """
    执行一条 ffmpeg 命令并等待结束。

    - 进程会注册到 registry，便于用户中途停止
    - 非 0 退出时抛出 RuntimeError，附带 stderr 最后几行，方便定位
    - 传了 on_progress（命令里要带 -progress pipe:1，见 build_command）时，
      边跑边把 ffmpeg 汇报的真实编码进度折算成 0~1 回调出去
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    registry.add(p)
    err_lines = []

    def drain_err():
        """stderr 必须一直读走：管道写满会把 ffmpeg 卡死在写日志上"""
        try:
            for line in p.stderr:
                err_lines.append(line)
        except Exception:
            pass

    err_thread = threading.Thread(target=drain_err, daemon=True)
    err_thread.start()
    try:
        if on_progress is not None and expected_dur > 0:
            for line in p.stdout:              # -progress pipe:1 的逐行输出
                frac = _read_out_time(line, expected_dur)
                if frac is None:
                    continue
                try:
                    on_progress(frac)
                except Exception:
                    pass
        else:
            p.stdout.read()                    # 不要进度也要读空，避免管道写满
        p.wait()
    finally:
        registry.discard(p)
        err_thread.join(timeout=3)
        for stream in (p.stdout, p.stderr):
            try:
                stream.close()
            except Exception:
                pass
    if p.returncode != 0:
        tail = b"".join(err_lines).decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError(" | ".join(tail[-3:])[:400] if tail else "ffmpeg 返回非 0")


# ============================================================================
# 五、画中画素材池（带磁盘缓存 + 并行扫描）
# ============================================================================

def _pool_cache_path(folder):
    """素材池缓存文件路径（放在素材目录内的隐藏文件）"""
    return os.path.join(folder, ".pip_cache.json")


# 进程内素材池缓存：folder -> (目录指纹, 素材列表)
# 作用：多个并发任务同时启动时，只有第一个真正扫描，其余直接复用内存结果
_POOL_MEM = {}
# 素材池记录的字段版本。加字段（如 v2 的宽高、v3 的旋转换算）时把它 +1，
# v4 将文件指纹的修改时间改为纳秒；旧元数据会重扫一次，不重建低清视频副本。
_POOL_CACHE_V = 4
_POOL_LOCKS = {}          # folder -> 扫描互斥锁（"单个飞行中"语义）
_POOL_LOCK = threading.Lock()


def _folder_lock(folder):
    """取某个素材目录专属的扫描锁（首次调用时惰性创建）"""
    with _POOL_LOCK:
        lk = _POOL_LOCKS.get(folder)
        if lk is None:
            lk = _POOL_LOCKS[folder] = threading.Lock()
        return lk


def _folder_signature(files):
    """
    计算目录指纹：文件名 → (大小, mtime)。
    只要文件增删或改动，指纹就会变化，缓存自动失效。
    """
    sig = {}
    for f in files:
        try:
            st = os.stat(f)
            sig[os.path.basename(f)] = [st.st_size, st.st_mtime_ns]
        except OSError:
            continue
    return sig


def scan_pip_pool(folder, log=None):
    """
    扫描画中画素材目录，返回 [{"path": 绝对路径, "dur": 时长秒}, ...]。

    性能要点（改动请谨慎）：
      - 进程内缓存：同一批任务只扫描一次（并发时靠锁保证不重复劳动）
      - 首次扫描：16 路线程池并行 probe（400 个素材约 1 秒）
      - 结果连同目录指纹写入 .pip_cache.json，二次启动毫秒级命中
      - 指纹使用体积和纳秒修改时间；目录变化只探测新增/修改的文件
      - 完全命中时不重写台账；删除的素材从新列表中移除

    :param folder: 素材目录
    :param log:    日志回调（可空）
    """
    files = _list_videos(folder)
    if not files:
        return []
    sig = _folder_signature(files)

    # --- 进程内缓存（并发任务共享，最快的路径） ---
    with _POOL_LOCK:
        mem = _POOL_MEM.get(folder)
    if mem and mem[0] == sig:
        return mem[1]

    # --- 加锁：确保同一时刻只有一个任务在扫描该目录，其余等结果 ---
    with _folder_lock(folder):
        with _POOL_LOCK:
            mem = _POOL_MEM.get(folder)
        if mem and mem[0] == sig:
            return mem[1]

        # 目录有变化时仍复用未改动文件的记录；只探测新增/变更的素材。
        cached = {"v": _POOL_CACHE_V, "signature": mem[0], "items": mem[1]} if mem else {}
        cache_file = _pool_cache_path(folder)
        if not cached:
            try:
                with open(cache_file, "r", encoding="utf-8") as fh:
                    cached = json.load(fh)
            except (OSError, ValueError):
                cached = {}
        if (not isinstance(cached, dict) or cached.get("v") != _POOL_CACHE_V
                or not isinstance(cached.get("signature"), dict)
                or not isinstance(cached.get("items"), list)):
            cached = {}
        old_sig = cached.get("signature") or {}
        file_set = set(files)
        reusable = {it["path"]: it for it in cached.get("items", [])
                    if isinstance(it, dict) and isinstance(it.get("path"), str)
                    and it["path"] in file_set
                    and isinstance(it.get("dur"), (int, float)) and it["dur"] > 0.6
                    and old_sig.get(os.path.basename(it["path"]))
                    == sig.get(os.path.basename(it["path"]))}

        # --- 并行探测 ---
        t0 = time.time()
        reused_count = len(reusable)

        def _one(path):
            try:
                d, w, h = probe_dur_size(path)
                if d > 0.6:
                    return {"path": path, "dur": float(d), "w": int(w), "h": int(h)}
            except Exception:
                pass
            return None

        pending = [path for path in files if path not in reusable]
        if pending:
            with ThreadPoolExecutor(max_workers=min(SCAN_THREADS, len(pending))) as ex:
                for r in ex.map(_one, pending):
                    if r:
                        reusable[r["path"]] = r
        items = [reusable[path] for path in files if path in reusable]

        if log and pending:
            log(f"已读取 {len(pending)} 个新增/变更素材，复用 {reused_count} 个缓存"
                f"（用时 {time.time() - t0:.1f} 秒）")

        with _POOL_LOCK:
            _POOL_MEM[folder] = (sig, items)

        if cached.get("signature") == sig and not pending:
            return items
        # --- 写缓存（失败不影响主流程） ---
        try:
            with open(cache_file, "w", encoding="utf-8") as fh:
                json.dump({"v": _POOL_CACHE_V, "signature": sig, "items": items}, fh)
        except Exception:
            pass
        return items


def _run_quiet(cmd, timeout=900):
    """
    跑一条命令，只要退出码（不注册进 registry、不抛异常）。

    专门给"素材加速副本"这类后台预处理用：单个素材失败不该打断整批，
    也不该混进底部"正在转码 N 个"的计数里（那个计数是给成片编码看的）。
    """
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode
    except Exception:
        return 1


def _small_short_side(main_w, main_h, opts):
    """
    算出素材低清副本的"短边"该取多少像素。

    目标：副本被裁成画中画之后，像素不能比实际显示尺寸还小，否则会糊。
        需要的像素 = 主视频尺寸 × 画中画占比 × 随机缩放上限 × 安全余量
    """
    zoom = max(1.0, float(opts.get("rnd_zoom", 1.0)))
    need = max(_even(main_w * opts["pip_w"]), _even(main_h * opts["pip_h"]))
    return max(PIP_SMALL_MIN_SHORT, _even(need * zoom * PIP_SMALL_HEADROOM))


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


def _is_small_copy(path):
    """这个素材路径是不是"低清副本"（就在 .pip_small/ 里）"""
    return os.path.basename(os.path.dirname(path)) == PIP_SMALL_DIR


def _build_small_copy(item, dst, short_side, hw=None):
    """
    生成单个素材的低清副本（供画中画拼接使用）。

    尺寸策略：按素材方向缩放**短边**（横屏缩高度、竖屏缩宽度），
    并且**只缩小、绝不放大**——放大只会增加解码量，不会多出任何信息。

    其它要点：
      - 去掉音轨（画中画全程静音，留着白占空间）
      - 关键帧间隔压到 PIP_SMALL_GOP，定位更快、切出来的起点更准
      - 先写 .part 临时文件，校验通过后才原子改名，
        避免"生成了半个文件、下次被当成可用副本"
      - 校验副本时长与原素材一致（不一致说明编码异常，宁可回退用原片）
    """
    w, h = int(item.get("w") or 0), int(item.get("h") or 0)
    vf = None
    if w and h:
        if w >= h:
            if h > short_side:
                vf = f"scale=-2:{short_side}"
        elif w > short_side:
            vf = f"scale={short_side}:-2"
    else:
        vf = f"scale=-2:{short_side}"

    # 临时文件名必须以 .mp4 结尾：ffmpeg 靠扩展名推断输出封装格式，
    # 写成 "xxx.mp4.part" 会直接报 "Unable to choose an output format"。
    tmp = dst + ".part.mp4"

    def make(accel):
        """拼一条完整的转码命令；accel 决定解码侧怎么走"""
        cmd = ([FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
               + accel + ["-i", item["path"]])
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-an", "-c:v", "libx264", "-preset", PIP_SMALL_PRESET,
                "-crf", str(PIP_SMALL_CRF), "-g", str(PIP_SMALL_GOP),
                "-pix_fmt", "yuv420p", "-threads", "2",
                "-movflags", "+faststart", tmp]
        return cmd

    def drop_tmp():
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    try:
        if _run_quiet(make(_small_accel_args(hw))) != 0:
            drop_tmp()
            # 硬解不可用（老机器）或该素材硬解失败 → 退回软解再试一次
            if _run_quiet(make(["-threads", "1"])) != 0:
                return
        d2 = probe_duration(tmp)
        if abs(d2 - float(item["dur"])) > max(0.3, float(item["dur"]) * 0.02):
            return
        os.replace(tmp, dst)
        return True
    except Exception:
        pass
    finally:
        drop_tmp()


def _stat_pair(path):
    """
    返回 (体积, 修改时间秒)。取不到就返回 (-1, -1)。

    台账靠这一对数字判断"源素材有没有被换过、副本有没有被外部改动过"，
    只 stat 不读文件内容，几百个素材也就几毫秒。
    """
    try:
        st = os.stat(path)
        return int(st.st_size), int(st.st_mtime)
    except OSError:
        return -1, -1


def small_dir_of(pip_dir):
    """素材目录 → 副本目录（两个地方都要用，集中一处免得写歪）"""
    return os.path.join(pip_dir, PIP_SMALL_DIR)


def _read_small_meta(small_dir):
    """
    读副本台账（副本目录里的 _meta.json）。

    台账只用来判断"要不要重建"，读不到 / 内容坏了都当空台账，
    顶多让下一次白重做一遍副本，绝不影响出片。
    """
    try:
        with open(os.path.join(small_dir, PIP_SMALL_META), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if isinstance(meta, dict) and isinstance(meta.get("items"), dict):
            meta.setdefault("v", PIP_SMALL_V)
            return meta
    except Exception:
        pass
    return {"v": PIP_SMALL_V, "items": {}}


def _write_small_meta(small_dir, meta):
    """原子写台账：先写 .tmp 再改名，避免写一半断电留下坏 JSON"""
    if meta == _read_small_meta(small_dir):
        return
    tmp = os.path.join(small_dir, PIP_SMALL_META + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(small_dir, PIP_SMALL_META))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _ensure_small_roster(small_dir):
    """在副本目录里放一份说明：这些文件是什么、会长期保留、删了会怎样"""
    p = os.path.join(small_dir, PIP_SMALL_ROSTER)
    if os.path.exists(p):
        return
    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "这个目录里放的是「画中画素材的低清副本」，是自动生成的加速缓存。\n"
                "\n"
                "为什么要有它：画中画在成片里只占约 260x384 像素，却要解码 1080p/4K 原片，\n"
                "画中画阶段的 93% 时间和 86% 内存都耗在这上面。先降成小尺寸副本再拼接，\n"
                "观感毫无差别，速度却能快十几倍。\n"
                "\n"
                "重要：这些副本**生成一次后会一直留着**，下次处理视频会直接复用，\n"
                "所以第二次开始几乎不用等。请不要手动删单个文件，也不要往这里放自己的东西。\n"
                "\n"
                "整个目录可以随时删掉——删了不会丢东西，工具下次会按需重新生成。\n"
                "（在「设置」里关掉加速副本，或直接删掉本目录，工具就会改用原素材。）\n"
            )
    except Exception:
        pass


def small_cache_stats(pip_dir):
    """
    只做 listdir + stat 的副本清点，返回 (已有副本数, 素材数, 副本总体积)。

    用于启动时的一句话汇报（"副本已经生成好了，会直接复用"）。
    刻意不读台账、不探测尺寸——启动阶段绝不能为了报个数去吃 CPU。
    """
    try:
        small = small_dir_of(pip_dir)
        have = size = 0
        if os.path.isdir(small):
            for f in os.listdir(small):
                if f.lower().endswith(".mp4"):
                    n = _stat_pair(os.path.join(small, f))[0]
                    if n > 4096:
                        have += 1
                        size += n
        return have, len(_list_videos(pip_dir)), size
    except Exception:
        return 0, 0, 0


def _small_names(pool, small_dir):
    """
    给每个素材定一个副本文件名，返回 {源素材: 副本路径}。

    默认直接用素材主名（xxx_raw.mp4 → xxx_raw.mp4），好认也好排错。
    只有当目录里出现"主名相同、扩展名不同"的素材（a.mp4 和 a.mov）时，
    才给它们加序号——否则两个素材会共用同一个副本文件，画中画会莫名其妙串素材。
    """
    dup = {}
    for it in pool:
        stem = os.path.splitext(os.path.basename(it["path"]))[0].lower()
        dup[stem] = dup.get(stem, 0) + 1
    used, out = {}, {}
    for it in pool:
        src = it["path"]
        stem = os.path.splitext(os.path.basename(src))[0]
        key = stem.lower()
        if dup.get(key, 1) > 1:
            used[key] = used.get(key, 0) + 1
            stem = f"{stem}.{used[key]}"
        out[src] = os.path.join(small_dir, stem + ".mp4")
    return out


def _small_plan(pool, small_dir, short_side):
    """
    把素材池分成「已有现成副本，直接调用」和「需要新建」两堆。全程只 stat，不解码。

    判定一个副本可以"直接调用"必须**同时**满足下面全部条件（差一条就重建）：

      1. 台账里有它的记录，且规格版本一致（工具升级改了副本规格后不会继续用旧的）
      2. 台账记的正是这个源素材（防止同名素材互相顶掉对方的副本）
      3. 源素材的体积 + 修改时间没变过（素材被替换过就失效）
      4. 副本文件的体积 + 修改时间与台账一致（被截断 / 被外部改动过就失效）
      5. 生成时用的短边 ≥ 这次需要的短边（后来把画中画调大了就得重做，否则会糊）

    另外兼容一种情况：副本是**以前版本生成、还没登记进台账**的。
    只要文件在、体积正常、且比源素材新，就直接认下来并补登记——
    否则用户一升级就会被逼着把几百个副本全部重做一遍，白白等好几分钟。

    **认领只发生在"台账里压根没有这条记录"时**（也就是纯粹的版本升级迁移）。
    如果台账里有记录但对不上（素材改了 / 尺寸要更大 / 副本被删了），一律重建——
    不能让认领逻辑把这些失效判断悄悄掩盖掉，否则调大画中画后永远用不到高清副本。

    :return: (mapping, todo, meta)
             mapping —— {源素材: 目标副本路径}（全部素材）
             todo    —— [(素材, 目标路径), ...]，空列表 = 全部已生成，直接调用
             meta    —— 台账（含本次补登记的条目，由调用方负责写回）
    """
    if not pool:
        return {}, [], _read_small_meta(small_dir)
    paths = {it["path"] for it in pool}
    # 编号沿用整目录的既有规则，不能随本次随机抽中的子集变化。
    names = paths | set(_list_videos(os.path.dirname(pool[0]["path"])))
    all_names = _small_names([{"path": path} for path in sorted(names)], small_dir)
    mapping = {path: all_names[path] for path in paths}
    meta = _read_small_meta(small_dir)
    ver_ok = meta.get("v") == PIP_SMALL_V
    if not ver_ok:
        # 规格升级后发布新版本；未登记的旧文件也不能再按历史副本认领。
        meta = {"v": PIP_SMALL_V, "items": {}, "legacy": False}
    items = meta["items"]

    todo = []
    for it in pool:
        src = it["path"]
        dst = mapping[src]
        key = os.path.basename(dst)
        ssz, smt = _stat_pair(src)
        dsz, dmt = _stat_pair(dst)

        rec = items.get(key)
        hit = bool(
            isinstance(rec, dict) and rec.get("src") == src and dsz > 4096
            and (ssz, smt) == (rec.get("size"), rec.get("mtime"))
            and (dsz, dmt) == (rec.get("out"), rec.get("outmtime"))
            and float(rec.get("short") or 0) >= float(short_side)
        )
        if (not hit and ver_ok and meta.get("legacy", True) and rec is None
                and dsz > 4096 and dmt >= smt and short_side <= PIP_SMALL_MIN_SHORT):
            # 老版本留下的副本（台账里完全没登记过）：认下来并补登记。
            # 注意 short 记的是下限值——我们无法得知当年按多大建的，
            # 记小了意味着"下次要求更大时会重建"，偏保守但不会出错。
            items[key] = {"src": src, "size": ssz, "mtime": smt,
                          "out": dsz, "outmtime": dmt,
                          "short": float(PIP_SMALL_MIN_SHORT)}
            hit = True
        if not hit:
            # 失败后保留失效标记，下一次必须重试，不能把旧文件认领成有效副本。
            items[key] = {"src": src, "short": 0}
            todo.append((it, dst))
    return mapping, todo, meta


def prepare_small_pool(pool, short_side, log=None, stop_event=None, threads=None,
                       progress_cb=None, hw=None):
    """同一素材目录串行核对/更新缓存，目录内的副本仍由线程池并行生成。"""
    if not pool:
        return {}
    # 多条主视频及后台预热会共用副本、临时文件和台账；必须先等前一批发布，
    # 再核对缓存，否则相同素材会重复转码，甚至因临时文件竞争回退到原片。
    folder = os.path.realpath(small_dir_of(os.path.dirname(pool[0]["path"])))
    # ponytail: 同目录冷缓存请求串行；若不重叠素材吞吐成为瓶颈，再拆逐文件锁和台账锁。
    lock = _folder_lock(folder)
    while not lock.acquire(timeout=0.1):
        if stop_event is not None and stop_event.is_set():
            return {}
    try:
        if stop_event is not None and stop_event.is_set():
            return {}
        return _prepare_small_pool(pool, short_side, log, stop_event, threads,
                                   progress_cb, hw)
    finally:
        lock.release()


def _prepare_small_pool(pool, short_side, log=None, stop_event=None, threads=None,
                        progress_cb=None, hw=None):
    """
    为素材池准备"低清副本"，返回 {原素材路径: 副本路径}。

    为什么值得做（逐项消融实测，48 段 / 3 并发，见 README 性能实测表）：
        用原素材拼接      13~14 秒，内存峰值 6.2 GB
        用低清副本拼接     1.0 秒，内存峰值 0.84 GB
    因为画中画在成片里最终只占约 260x384 像素，解码 1080p 素材纯属浪费。

    **副本是长期缓存，不会被删**：生成一次后一直留在 <素材目录>/.pip_small/ 里，
    本函数每次运行都会先按台账盘点一遍——已经生成好的直接拿来用（只 stat，不解码、
    不转码），只有缺的 / 过期了的才新建。所以第二次以后基本是秒过。

    容错：素材目录不可写时仍使用已验证的副本；仅缺失或生成失败的素材
    回退原片，绝不把失败重建留下的旧文件标成有效缓存。

    :param pool      : scan_pip_pool 的结果（元素含 path/dur/w/h）
    :param short_side: 副本短边像素（见 _small_short_side）
    :param progress_cb: 可选的进度回调 (已完成数, 总数)，用于驱动进度条而不是刷日志
    :param hw       : 硬件加速模式（见 hw_decode_args），None = 用默认
    :return: {原素材: 副本}；不适用时返回 {}
    """
    if not pool:
        return {}
    folder = os.path.dirname(pool[0]["path"])
    small_dir = small_dir_of(folder)

    # ---- 先盘点：哪些早就生成好了（这一步不转码，几百个素材几毫秒） ----
    t0 = time.time()
    mapping, todo, meta = _small_plan(pool, small_dir, short_side)
    ready = len(mapping) - len(todo)
    if progress_cb is not None:
        progress_cb(ready, len(pool))

    # ---- 全都现成：直接调用，一个字节都不重做 ----
    if not todo:
        if log:
            log(f"   低清副本：{ready}/{len(pool)} 个已生成，直接复用"
                f"（核对用时 {time.time() - t0:.2f} 秒）")
        _write_small_meta(small_dir, meta)
        return mapping

    pending = {it["path"] for it, _ in todo}
    ok = {src: dst for src, dst in mapping.items() if src not in pending}
    # ---- 只在需要生成时探测写权限；只读目录仍复用已验证的副本 ----
    try:
        os.makedirs(small_dir, exist_ok=True)
        probe = os.path.join(small_dir, ".write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
    except Exception:
        if log:
            log("   素材目录不可写，复用已有有效副本，缺失素材回退原片")
        return ok

    _ensure_small_roster(small_dir)

    if log:
        if ready:
            log(f"   低清副本：已有 {ready} 个可直接用，新建 {len(todo)} 个"
                f"（每个只做一次，之后一直复用）…")
        else:
            log(f"   首次用到这 {len(todo)} 个素材，正在建立低清副本"
                f"（只做一次，之后一直复用）…")

    items = meta["items"]
    t1 = time.time()
    done = [0]
    lock = threading.Lock()

    def one(job):
        if stop_event is not None and stop_event.is_set():
            return
        it, dst = job
        built = False
        try:
            built = _build_small_copy(it, dst, short_side, hw)
        except Exception:
            pass
        dsz, dmt = _stat_pair(dst)
        ssz, smt = _stat_pair(it["path"])
        with lock:
            if built and dsz > 4096:
                ok[it["path"]] = dst
                items[os.path.basename(dst)] = {
                    "src": it["path"], "size": ssz, "mtime": smt,
                    "out": dsz, "outmtime": dmt, "short": float(short_side)}
            done[0] += 1
            # 在锁内按顺序发送，避免并发完成时进度倒退。
            if progress_cb is not None:
                progress_cb(ready + done[0], len(pool))

    with ThreadPoolExecutor(max_workers=max(1, int(threads or PIP_SMALL_THREADS))) as ex:
        list(ex.map(one, todo))

    # ---- 台账落盘：以后就靠它判断"已生成、可直接调用" ----
    _write_small_meta(small_dir, meta)
    if log:
        log(f"   低清副本：本次新建 {len(ok) - ready}/{len(todo)} 个，"
            f"用时 {time.time() - t1:.1f} 秒，未成功的回退原片。")
    return ok


# 已在做"整池补齐"的素材目录（避免重复开线程）
_SMALL_WARMING = set()
_SMALL_WARM_LOCK = threading.Lock()


def _warm_small_pool_async(folder, main_info, opts, log=None):
    """
    后台把整池素材的加速副本补齐（不占用本次任务的等待时间）。

    为什么需要：只建"本次抽到的"素材能让当前任务立刻开跑，但下次随机抽到别的
    素材又得重新等十几秒。所以在全部视频处理完之后，用低并发把剩下的慢慢补上，
    等用户下次点开始时整池都已就绪，几乎是秒开。

    中断无害：副本是逐个原子落盘的，中途退出只是没补完，下次接着补。
    """
    with _SMALL_WARM_LOCK:
        if folder in _SMALL_WARMING:
            return
        _SMALL_WARMING.add(folder)

    def work():
        try:
            pool = scan_pip_pool(folder)
            if not pool:
                return
            short = _small_short_side(main_info["width"], main_info["height"], opts)
            if log:
                log("需要补建的副本：后台正在处理（不影响使用，下次会快很多）…")
            # 并发压到 2：这是"搭便车"的活，不能跟用户正在跑的活抢机器
            got = prepare_small_pool(pool, short, None, opts.get("stop_event"), threads=2,
                                     hw=opts.get("hwaccel"))
            if log and len(got) == len(pool):
                log("低清副本已补齐，以后处理视频会更快。")
        except Exception:
            pass
        finally:
            with _SMALL_WARM_LOCK:
                _SMALL_WARMING.discard(folder)

    threading.Thread(target=work, daemon=True).start()


# ============================================================================
# 六、画中画素材轨构建
# ============================================================================

def _clip_filter(pw, ph, zoom, flip_h, color_on, color_params, fps_str, crop_fill, speed=1.0):
    """
    生成单个画中画片段的视频滤镜链（字符串）。

    处理顺序说明：
        0) setpts             —— 变速（PTS/speed，配合前面的 -t 实现"加速播放"）
        1) hflip              —— 随机水平镜像
        2) 统一尺寸 + 居中裁剪 —— zoom 时会先放大再裁回，产生像素位移
        3) eq 调色             —— 亮度/对比度/饱和度的极小扰动
        4) fps / setsar / format —— 与主视频对齐，保证可拼接

    :param pw, ph      : 目标画中画尺寸
    :param zoom        : 缩放系数（>=1.0；1.0 表示不缩放）
    :param flip_h      : 是否水平翻转
    :param color_on    : 是否启用随机调色
    :param color_params: (brightness, contrast, saturation) 三元组
    :param fps_str     : 主视频帧率字符串
    :param crop_fill   : True=裁剪填满；False=保持比例加黑边
    :param speed       : 变速倍数，1.0 表示不变速
    """
    parts = []
    # ---- 色彩统一：素材池里混着 bt709 / bt2020(HLG) / bt470bg(601) 三种。
    # 它们被拼到同一条画中画轨以后，ffmpeg 播到色彩切换点就会
    # "Reconfiguring filter graph because video parameters changed"，
    # 滤镜图一重配置，主合成的帧时间戳就错乱——实测成片视频轨只剩源的一半
    # （日志 drop=3643~6296 帧），播到两分钟画面卡死、只剩声音。
    # 所以这里在缩放的同时把像素**真正转换**到 bt709（不是只改标记，
    # 只改标记会让 601 素材偏色），末尾再用 setparams 把标记也统一。
    cm = ":out_color_matrix=bt709"
    # 统一时间基准：无论从哪里截取，都让片段从 0 时刻开始，拼接时才不会出现空隙。
    # 变速（加速）就是在这里实现的：PTS 除以倍数，播放器就会用更短的时间放完同样的画面。
    parts.append(f"setpts=(PTS-STARTPTS)/{max(0.01, speed):.6f}")
    if flip_h:
        parts.append("hflip")

    # ---- 尺寸统一：无论走哪条分支，最后必然得到 pw×ph 的画面 ----
    # 这一点不能省：
    #   1) 所有片段尺寸必须完全一致，否则后面无法拼接；
    #   2) h264 要求宽高都是偶数，而"等比放大到覆盖目标"会算出奇数边
    #      （例：3840x2160 的素材填 258x384 → 683x384，683 是奇数 → 编码器直接报错）。
    #      所以放大之后必须 crop 回目标尺寸；走"完整显示"模式则用 pad 补齐。
    if crop_fill:
        if zoom > 1.0001:
            # 随机缩放：先放大到略大于目标，再裁回，画面像素发生位移（抗查重）
            parts.append(f"scale={_even(pw * zoom)}:{_even(ph * zoom)}"
                         f":force_original_aspect_ratio=increase{cm}")
        else:
            parts.append(f"scale={pw}:{ph}:force_original_aspect_ratio=increase{cm}")
        parts.append(f"crop={pw}:{ph}")
    else:
        parts.append(f"scale={pw}:{ph}:force_original_aspect_ratio=decrease{cm}")
        parts.append(f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:black")

    if color_on:
        b, c, s = color_params
        parts.append(f"eq=brightness={b:.4f}:contrast={c:.4f}:saturation={s:.4f}")

    parts += [f"fps={fps_str}", "setsar=1", "format=yuv420p",
              # 与上面的色彩统一配套：把标记也写成 bt709
              "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"]
    return ",".join(parts)


def _plan_pip_segments(main, opts, pool, target=None):
    """
    规划画中画片段（纯计算，不启动任何 ffmpeg，毫秒级完成）。

    规则：
        - 素材洗牌后依次取用，**同一轮内不重复**；用尽则重新洗牌循环复用
        - 每段在"掐头去尾后的有效区间"内随机取起点、随机时长
        - 每段独立随机镜像 / 缩放 / 调色
        - 直到累计时长够目标长度为止

    时长换算：成片贡献时长 = 截取素材长度 ÷ 加速倍数，故 need_src = out_seg * speed

    :param target: 需要凑够的总时长（秒）；默认取主视频时长。
                   有片段因素材损坏被丢弃时，调用方会传一个补足值再规划一轮。
    :return: (specs, reused, pw, ph)
             specs  : [{"path","start","need_src","out_seg","vf"}, ...]，顺序即最终播放顺序
             reused : 是否发生过"素材用尽后循环复用"
    """
    T = main["duration"] if target is None else float(target)
    w, h, fps = main["width"], main["height"], main["fps_str"]
    pw = _even(w * opts["pip_w"])             # 画中画实际像素尺寸
    ph = _even(h * opts["pip_h"])
    crop_fill = opts["pip_fill"] == "crop"
    speed = opts["pip_speed"]
    head, tail = opts["pip_head"], opts["pip_tail"]

    pool = list(pool)                  # 缓存列表由并发任务共享，只打乱本次的顺序
    random.shuffle(pool)
    total, specs, reused, i = 0.0, [], False, 0

    while total < T - 0.05 and len(specs) < MAX_SEGMENTS:
        # ---- 素材轮换：用尽则重新洗牌（尽量把重复间隔拉大） ----
        if i >= len(pool):
            random.shuffle(pool)
            i = 0
            reused = True
        item = pool[i]
        i += 1

        src_dur = item["dur"]

        # ---- 掐头去尾：算出素材的"有效区间" ----
        lo = src_dur * head                   # 有效起点（默认 10% 处）
        hi = src_dur * (1.0 - tail)           # 有效终点（默认 90% 处）
        usable = hi - lo
        if usable < 0.5:                      # 素材太短，退化为整段可用
            lo, usable = 0.0, src_dur

        # ---- 决定这一段要"贡献"多长成片时间，并换算成需要截取的素材长度 ----
        remain = T - total
        out_seg = min(random.uniform(SEG_MIN, SEG_MAX), remain)
        need_src = min(out_seg * speed, usable)
        out_seg = need_src / speed            # 受素材长度限制后的实际贡献时长
        if out_seg < 0.05:                    # 兜底，避免产生 0 长度片段
            break

        # ---- 在有效区间内随机取起点，保证不越界 ----
        start = lo + random.uniform(0.0, max(0.0, usable - need_src - 0.05))

        # ---- 随机化参数（逐段独立随机，抗平台查重） ----
        zoom = random.uniform(1.0, opts["rnd_zoom"]) if opts["rnd_zoom"] > 1.0001 else 1.0
        color_params = (
            random.uniform(-0.020, 0.020),    # brightness 亮度微扰
            random.uniform(0.980, 1.020),     # contrast   对比度微扰
            random.uniform(0.980, 1.020),     # saturation 饱和度微扰
        )
        vf = _clip_filter(pw, ph, zoom,
                          opts["rnd_flip_h"] and random.random() < 0.5,
                          opts["rnd_color"], color_params,
                          fps, crop_fill, speed)

        specs.append({"path": (opts.get("small_map") or {}).get(item["path"])
                              or item["path"],
                      "start": start, "need_src": need_src,
                      "out_seg": out_seg, "vf": vf})
        total += out_seg

    return specs, reused, pw, ph


def _encode_pip_batch(batch, dst, registry, hw=None):
    """
    用一个 ffmpeg 进程把一批片段拼成一个文件（多输入 + concat 滤镜）。

    这是画中画性能的核心：早期实现是"一段一个进程、串行跑"，
    9 分钟视频要启动 100+ 次 ffmpeg，进程启停开销比编码本身还大。
    现在每批 8 段只起 1 个进程，批内由 ffmpeg 自动并行解码，批次之间再并行。

    命令行形态（每段：定位 → 读一小段 → 各自套滤镜 → 顺序拼接）：
        -ss 起点 -t 长度 -i 素材1  -ss … -t … -i 素材2  …
        -filter_complex "[0:v]滤镜[v0];[1:v]滤镜[v1];…;[v0][v1]…concat=n=N:v=1:a=0[o]"
        -map [o] -an -c:v libx264 … batch.mp4

    :param batch   : _plan_pip_segments 产出的片段规格列表（本批）
    :param dst     : 本批输出文件
    :param registry: 进程注册表（用于"停止"时终止 ffmpeg）
    :param hw      : 硬件加速模式（见 hw_decode_args）。这一批解的是 480p 副本，
                     实测开硬解反而慢 60%，所以"自动"在这里不会开（真正会用硬解的
                     是生成副本那一步）；"开启"才会强行开。
    """
    accel = hw_decode_args(hw, HW_STAGE_PIP_SMALL
                           if (batch and all(_is_small_copy(sp["path"]) for sp in batch))
                           else HW_STAGE_PIP_RAW)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    for sp in batch:
        # -ss / -t 写在 -i 之前 = 输入选项：只解码需要的那一小段，速度最快
        cmd += accel + ["-ss", f"{sp['start']:.3f}", "-t", f"{sp['need_src']:.3f}",
                        "-i", sp["path"]]

    branches = "".join(f"[{i}:v]{sp['vf']}[s{i}];" for i, sp in enumerate(batch))
    inputs = "".join(f"[s{i}]" for i in range(len(batch)))
    fc = f"{branches}{inputs}concat=n={len(batch)}:v=1:a=0[o]"

    cmd += ["-filter_complex", fc, "-map", "[o]",
            "-an",                                     # 画中画永远静音
            "-c:v", "libx264", "-preset", PIP_BATCH_PRESET, "-crf", str(CRF),
            "-pix_fmt", "yuv420p",
            # 与 _clip_filter 末尾的 setparams 配套：把色彩标记写进容器/SPS，
            # 这样 concat demuxer 用 -c copy 拼出来的整条画中画轨都是纯 bt709，
            # 不会再出现 HDR/bt709 交替导致主合成重配置滤镜图（详见 _clip_filter）。
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-threads", str(PIP_BATCH_THREADS),
            dst]
    _run(cmd, registry)


def _encode_pip_batches(specs, tmpdir, opts, registry, log, prog=None):
    """
    分批编码所有片段，批次之间并行执行（并行度由 opts["pip_parallel"] 决定）。

    并行度在 _start 里按"CPU 核心数 ÷ 实际同时处理的视频数"算好，
    保证多个视频同时处理时不会把核心全部抢光。

    容错：某个批次失败时（个别素材损坏、格式异常等），不再整批放弃，
    而是把该批拆成单段逐个重试，只丢弃真正跑不通的那几段，并把它们回传给调用方，
    由调用方补足时长——这样一条坏素材不会让整个视频丢掉画中画。

    :return: (batch_files, failed_specs)
             failed_specs 里是没编成功的片段（调用方据此补时长）
    """
    batches = [specs[i:i + PIP_BATCH] for i in range(0, len(specs), PIP_BATCH)]
    files = [os.path.join(tmpdir, f"b{i:03d}.mp4") for i in range(len(batches))]
    parallel = max(1, min(len(batches), int(opts.get("pip_parallel", 1) or 1)))
    hw = opts.get("hwaccel")

    done, failed = 0, []

    def note(n):
        """批次进度只喂进度条，不写日志（用户要求：不要一行一行刷屏）"""
        if prog is not None:
            prog.count("pip", n, len(batches))   # 真实批次数 → 真实进度

    def run_batch(batch, path, bid):
        _check_stopped(opts)
        try:
            _encode_pip_batch(batch, path, registry, hw)
            return []
        except Exception as e:
            _check_stopped(opts)
            # 整批失败 → 拆开逐段重试，定位到具体是哪几段有问题
            log(f"   有素材无法编码，正在逐段排查（{e}）")
            if os.path.exists(path):
                os.remove(path)       # 失败残留不能混进后面的 stream copy
            ok = []
            bad = []
            for i, sp in enumerate(batch):
                _check_stopped(opts)
                seg_path = os.path.join(tmpdir, f"fix_{bid:03d}_{i:02d}.mp4")
                try:
                    _encode_pip_batch([sp], seg_path, registry, hw)
                    ok.append(seg_path)
                except Exception:
                    _check_stopped(opts)
                    bad.append(sp)
            if len(ok) == 1:
                os.replace(ok[0], path)          # 只剩一段时直接改名复用，省一次拼接
            elif ok:
                _concat_pip_batches(ok, path, registry)
            return bad

    if parallel == 1 or len(batches) == 1:
        for bid, (batch, path) in enumerate(zip(batches, files)):
            failed += run_batch(batch, path, bid)
            done += 1
            note(done)
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futures = {ex.submit(run_batch, b, p, bid): p for bid, (b, p) in enumerate(zip(batches, files))}
            for fut in as_completed(futures):
                failed += fut.result()
                done += 1
                note(done)

    # 整批和逐段都没救回来的批次，文件不存在 → 从结果里剔除，避免拼接时报错
    return [f for f in files if os.path.exists(f) and os.path.getsize(f) > 0], failed


def _concat_pip_batches(files, dst, registry):
    """
    把各批文件顺序拼成完整画中画轨。

    各批由同一套参数（同尺寸/帧率/像素格式）编码，因此可以直接 stream copy 拼接，
    不需要再解码重编码（秒级完成）。
    """
    if len(files) == 1:
        return files[0]
    listfile = os.path.join(os.path.dirname(files[0]), "list.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for path in files:
            # 路径统一写成正斜杠：concat demuxer 把反斜杠当转义符，
            # Windows 下的 "C:\...\b000.mp4" 会被解析坏掉（报 No such file）。
            # ffmpeg 在 Windows 上也接受正斜杠，所以两种系统都安全。
            safe = path.replace("\\", "/").replace("'", "'\\''")
            fh.write("file '%s'\n" % safe)
    _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
          "-f", "concat", "-safe", "0", "-i", listfile, "-an", "-c", "copy", dst], registry)
    return dst


def build_pip_track(main, opts, tmpdir, registry, log, prog=None):
    """
    为一条主视频生成画中画轨（静音），返回 (文件路径, 宽, 高)。

    三段式流程（每步都很轻）：
        1. 规划：算出所有片段（纯计算，不碰 ffmpeg）
        2. 编码：按 PIP_BATCH 分批，每批一个 ffmpeg 进程，批次并行
        3. 拼接：各批 stream copy 成一条轨

    :param main    : 主视频的 probe_media 结果
    :param opts    : 全局选项字典（见 App._start）
    :param tmpdir  : 临时工作目录（调用方负责清理）
    :param registry: 进程注册表
    :param log     : 日志回调
    :param prog    : VideoProgress（进度条用，可空）；这里喂它副本数与批次数
    """
    pool = scan_pip_pool(opts["pip_dir"], log)
    if not pool:
        raise RuntimeError(f"素材目录里没有可用的小视频：{opts['pip_dir']}")

    specs, reused, pw, ph = _plan_pip_segments(main, opts, pool)
    if not specs:
        raise RuntimeError("没有规划出任何画中画片段")

    # ---- 素材低清副本（实测最大的提速项，理由见 PIP_SMALL_DIR 上方注释） ----
    # 画中画在成片里只占约 260x384 像素，却要解码 1080p 素材——这是画中画阶段
    # 93% 的时间与 86% 的内存开销所在。先降成小尺寸副本再拼接，观感毫无差别。
    #
    # 副本是**长期缓存**：已经生成过的会被直接认出来复用（只核对，不转码），
    # 只有"没生成过 / 素材被换过 / 画中画尺寸调大了"的才会新建。见 prepare_small_pool。
    #
    # 副本映射通过本视频私有的 opts["small_map"] 传给 _plan_pip_segments。
    # 绝不能写成"挂到 pool 里的素材字典上"——那份 pool 是进程内缓存的、还被其它
    # 并发视频共享，改了会串（踩过的坑：第二条视频开始 specs 直接指向副本，
    # used 过滤成空集，于是副本核对被整个跳过）。
    opts = dict(opts)                  # 本视频私有副本，避免往共享字典里写东西
    small = {}
    if opts.get("pip_small", True):
        used = {sp["path"] for sp in specs}

        def copy_note(n, m):
            """副本进度按真实个数推进度条"""
            if prog is not None:
                prog.count("copy", n, m)

        small = prepare_small_pool(
            [it for it in pool if it["path"] in used],
            _small_short_side(main["width"], main["height"], opts),
            log, opts.get("stop_event"), hw=opts.get("hwaccel"),
            progress_cb=(copy_note if prog else None)) or {}
        opts["small_map"] = small

    # 规划发生在建副本之前，所以要把已经规划好的片段改指向副本；
    # 后面"兜底补段"会重新规划一轮，那时会直接通过 small_map 拿到副本路径。
    for sp in specs:
        dst = small.get(sp["path"])
        if dst:
            sp["path"] = dst

    batches = (len(specs) + PIP_BATCH - 1) // PIP_BATCH
    log(f"   准备画中画：从 {len(pool)} 个素材里抽取 {len(specs)} 段，"
        f"共 {batches} 批" + ("（素材不够，已循环复用）" if reused else "（素材不重复）"))

    files, failed = _encode_pip_batches(specs, tmpdir, opts, registry, log, prog)

    # ---- 兜底补段：个别素材坏了、编不出来时，用别的素材把缺掉的时长补回来 ----
    # 最多补两轮，避免极端情况下反复重试。补出来的段直接接在末尾——
    # 画中画本来就是随机顺序，接在后面不影响观感。
    for attempt in range(2):
        if not failed:
            break
        missing = sum(sp.get("out_seg", 0.0) for sp in failed)
        log(f"   有 {len(failed)} 段素材不可用，正在用其它素材补上这 {missing:.1f} 秒")
        if missing < 0.1:
            break
        extra, _, _, _ = _plan_pip_segments(main, opts, pool, target=missing + 0.3)
        if not extra:
            break
        patch_dir = os.path.join(tmpdir, f"patch{attempt}")
        os.makedirs(patch_dir, exist_ok=True)
        more_files, failed = _encode_pip_batches(extra, patch_dir, opts, registry, log, prog)
        files += more_files
        # 补段如果也失败，下一轮继续补；两轮之后仍失败的就不再纠缠
        if attempt == 1 and failed:
            log(f"   {len(failed)} 段素材始终无法使用，已跳过")

    if not files:
        raise RuntimeError("所有画中画素材都无法编码")
    pip_file = _concat_pip_batches(files, os.path.join(tmpdir, "pip.mp4"), registry)
    return pip_file, pw, ph


# ============================================================================
# 七、主合成命令
# ============================================================================

def build_command(src, opts, info, dst, pip=None):
    """
    构造最终合成的 ffmpeg 命令。

    滤镜拓扑（一路到底，只编码一次）：
        [1:v] 首帧图 ──┐
                       ├─ concat ─→ 主画面 ─┐
        [0:v] 原片第2帧起 ┘                    ├─ overlay 产品图（可选） ─ overlay 画中画（可选） ─→ 输出
        [2:v] 产品图 ─────────────────────────┘
        [3:v] 画中画轨 ───────────────────────────────────┘

    商品图与画中画都是**可选的**：商品图由"显示概率"决定（见 _decide_product），
    画中画由界面开关决定。谁缺席，谁那一路输入和那段 overlay 就整段不生成，
    后面的输入索引跟着前移（首图恒为 [1:v]，商品图 [2:v]，画中画 [2:v] 或 [3:v]）。

    :param src  : 主视频路径
    :param opts : 选项字典
    :param info : 主视频 probe 结果
    :param dst  : 输出路径
    :param pip  : (画中画文件, 宽, 高) 或 None
    返回 (命令列表, 产品图输出宽, 产品图输出高)；没有商品图时宽高都是 0
    """
    w, h, fps = info["width"], info["height"], info["fps_str"]
    # 商品图是**可选**的：概率为 0 或这一条掷骰未命中时，opts 里没有 product，
    # 直接把这道工序整段省掉（不叠透明图，也不多喂一路输入）。
    use_prod = bool(opts.get("product")) and bool(opts.get("prod_size"))

    # ---- 输入索引按实际用到的输入排 ----
    # [0]=主视频   [1]=首图   [2]=商品图（可选）   [2 或 3]=画中画（可选）
    # 必须跟着条件一起算：写死 [2:v] 会指到画中画轨上，成片会出现"商品图位置
    # 变成一片画中画"这种莫名其妙的错位。
    cover_i = 1
    prod_i = 2 if use_prod else None
    pip_i = (3 if use_prod else 2) if pip else None

    target_w = target_h = 0
    if use_prod:
        pw_src, ph_src = opts["prod_size"]
        # ---- 产品图目标尺寸：宽 = 视频宽 * 1/4，等比；过高时按可用高度回缩 ----
        target_w = _even(w * PRODUCT_WIDTH_RATIO)
        target_h = _even(ph_src * target_w / pw_src)
        max_h = max(2, h - MARGIN_BOTTOM * 2)
        if target_h > max_h:
            target_h = _even(max_h)
            target_w = _even(pw_src * target_h / ph_src)

    overlay_from = max(0, int(opts.get("prod_start", PRODUCT_START_FRAME)))   # 产品图从第几帧开始显示
    fc = (
        # 首帧：图片拉伸到主视频尺寸，只取 1 帧，帧率对齐
        f"[{cover_i}:v]scale={w}:{h},setsar=1,format=yuv420p,fps={fps},"
        f"trim=end_frame=1,setpts=PTS-STARTPTS[first];"
        # 剩余帧：主视频从第 2 帧开始
        f"[0:v]trim=start_frame=1,setpts=PTS-STARTPTS,setsar=1,format=yuv420p[rest];"
        f"[first][rest]concat=n=2:v=1:a=0[vcat]"
    )
    last = "[vcat]"
    if use_prod:
        fc += (
            # 产品图：缩放到目标尺寸，保留透明度
            f";[{prod_i}:v]scale={target_w}:{target_h},format=rgba,setsar=1[prod];"
            # 产品图 overlay：水平居中，距底部 MARGIN_BOTTOM
            f"{last}[prod]overlay=x=(W-w)/2:y=H-h-{MARGIN_BOTTOM}:"
            f"enable='gte(n,{overlay_from})'[v2]"
        )
        last = "[v2]"
    if pip:
        pip_file, pip_w, pip_h = pip
        # 位置随机微抖动（±N px），抗哈希；对观感几乎无影响
        j = max(0, int(opts["rnd_jitter"]))
        jx = random.randint(-j, j) if j else 0
        jy = random.randint(-j, j) if j else 0
        right = _even(w * opts["pip_right"]) + jx
        top = _even(h * opts["pip_top"]) + jy
        fc += (
            f";[{pip_i}:v]scale={pip_w}:{pip_h},setsar=1,fps={fps},format=yuv420p[pip];"
            # eof_action=repeat：若画中画轨比主视频短，定格最后一帧而不是消失
            f"{last}[pip]overlay=x=W-w-{right}:y={top}:eof_action=repeat[vout]"
        )
        last = "[vout]"
    else:
        fc += f";{last}null[vout]"
        last = "[vout]"

    # ---- 组装命令行 ----
    # 硬件解码只加在**视频**输入上（首图/主图是单张图，走硬解没意义还可能失败）。
    # 实测主合成阶段开不开硬解无差别，所以"自动"在这里不开，见 hw_decode_args。
    hw = hw_decode_args(opts.get("hwaccel"), HW_STAGE_MAIN)
    # -progress pipe:1：让 ffmpeg 把"已编码多少秒"逐行写到 stdout，界面据此显示
    # 真实进度（解析见 _read_out_time）。日志仍在 stderr，两者互不干扰。
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-nostats", "-progress", "pipe:1"]
    cmd += hw + ["-i", src]
    cmd += ["-i", opts["cover"]]
    if use_prod:
        cmd += ["-i", opts["product"]]
    if pip:
        cmd += hw + ["-i", pip[0]]
    cmd += ["-filter_complex", fc, "-map", last]
    if info["has_audio"]:
        cmd += ["-map", "0:a?"]
        # 音频：aac/mp3 直接复制，其它格式转 aac（避免容器不兼容）
        cmd += ["-c:a", "copy"] if all(c in ("aac", "mp3") for c in info["acodecs"]) \
            else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", opts["preset"], "-crf", str(CRF),
            "-pix_fmt", "yuv420p",
            "-threads", str(opts["threads"])]
    if info["duration"] > 0:
        cmd += ["-t", f"{info['duration']:.3f}"]     # 输出长度锁死为主视频长度
    cmd += ["-movflags", "+faststart", "-avoid_negative_ts", "make_zero", dst]
    return cmd, target_w, target_h


def _clamp_prod_start(opts, info, say=None):
    """
    把「产品图起始帧」夹到视频真实帧数范围内，返回一份**新的** opts（不改调用方那份）。

    背景：起始帧设成 60，而某个视频只有 30 帧（1 秒）时，overlay 的 enable 条件
    永远不成立，产品图整条都不会出现，用户会以为功能坏了。这里退到最后一帧并如实说明。

    :param opts: 本批共用的选项字典（多线程共享，**绝不能就地改**）
    :param info: probe_media 的结果
    :return: 原字典（无需调整）或调整后的副本
    """
    start = max(0, int(opts.get("prod_start", PRODUCT_START_FRAME)))
    fps = float(info.get("fps") or 0)
    dur = float(info.get("duration") or 0)
    frames = int(dur * fps) if fps > 0 and dur > 0 else 0
    if frames and start >= frames:
        fixed = max(0, frames - 1)
        if say:
            say(f"说明：产品图起始帧 {start} 超出这个视频的总帧数（{frames} 帧），"
                f"已从第 {fixed} 帧起显示")
        start = fixed
    if start == opts.get("prod_start"):
        return opts
    new = dict(opts)
    new["prod_start"] = start
    return new


def _decide_product(opts):
    """
    按「商品图显示概率」为本条视频掷骰，返回一份**新的** opts。

    概率语义（每条视频独立掷骰，不是整批抽签）：
        0   → 一定不叠加商品图
        100 → 一定叠加（从主图目录里随机抽一张）
        40  → 40% 的概率叠加

    未命中时把 `product_files` 置空（下游据此省掉一路输入、一段 overlay 和一次
    图片尺寸探测），而不是换一张透明图去叠——那样每帧仍要跑一次 alpha 混合。

    必须在 `_pick_images` **之前**调用，否则会白探一次用不到的图片尺寸。
    返回副本的原因与 `_pick_images` 相同：opts 被同批所有并发视频共享。
    """
    try:
        chance = int(opts.get("prod_chance", PRODUCT_CHANCE))
    except Exception:
        chance = PRODUCT_CHANCE
    chance = max(0, min(100, chance))
    if chance >= 100:                       # 常见情况：不掷骰子，省掉一次随机数
        return opts
    if chance > 0 and random.random() * 100 < chance:
        return opts
    new = dict(opts)
    new["product_files"] = []               # 这条视频不走商品图分支
    new["product"] = ""
    new["prod_size"] = None
    return new


def _pick_images(opts):
    """
    为**当前这条视频**随机固定首图与主图，返回一份新的 opts。

    需求：首图和主图各自放在一个目录里，每条视频从目录里随机各挑一张；
    同一条视频全程只用挑中的那两张——中途不再换图，否则观众能看出来。

    只传单个文件时等价于"目录里只有这一张"，行为与以前完全一致。
    首图与主图**各自独立**处理：商品图概率为 0 时主图目录会是空的，
    这时首图仍然要照常抽（首帧图是成片的必要拼图，与商品图无关）。

    注意返回的是**副本**：opts 由多个并发视频共享，绝不能就地修改。
    两边都取不到图时等价于原样返回，由上游的参数校验负责提示。

    挑中的文件名记在返回值的 picked_cover / picked_product 里，
    由调用方拼进"完成"那一行——这样每条视频只占一行日志，信息也不丢。
    """
    covers = opts.get("cover_files") or list_images(opts.get("cover"))
    prods = opts.get("product_files") or list_images(opts.get("product"))
    if not covers and not prods:
        return opts

    new = dict(opts)
    if covers:
        new["cover"] = random.choice(covers)
        new["picked_cover"] = os.path.basename(new["cover"])
    if prods:
        new["product"] = random.choice(prods)
        try:
            new["prod_size"] = probe_image_size(new["product"])
        except Exception:
            new["prod_size"] = opts.get("prod_size")   # 读不到尺寸就沿用外面给的
        new["picked_product"] = os.path.basename(new["product"])
    elif opts.get("product") and not os.path.isfile(opts.get("product") or ""):
        # 目录里一张可用图片都没有：宁可这条不叠商品图，也不能把"目录路径"
        # 当成图片喂给 ffmpeg（那会是一条看不懂的输入错误）
        new["product"] = ""
        new["prod_size"] = None
    return new


def _safe_probe(path):
    """探测视频信息，失败返回 None（预检阶段用，绝不因为单个文件异常打断整批）"""
    try:
        return probe_media(path)
    except Exception:
        return None


class VideoProgress:
    """
    把一条视频**各阶段的真实完成量**折算成 0~1 的总完成度，供进度条使用。

    这里刻意不做"按时间猜"的平滑动画：只有真的干完了一步，进度才往前走。
    所以进度条不会跑在事实前面，任务卡住时也不会自己偷偷往前爬。

    三个阶段的完成量全部是实测数字，没有一个估计值：
        副本   —— prepare_small_pool 报的「已生成 / 需生成」个数
        画中画 —— _encode_pip_batches 报的「已完成批数 / 总批数」
        合成   —— ffmpeg 用 -progress 自己报的 out_time / 成片时长
    阶段之间按经验耗时占比加权（合成最重）；没开画中画时只剩合成一个阶段，
    权重自动归一，不会在开头留出一段走不动的空档。
    """

    _PIP_PLAN = (("copy", 0.15), ("pip", 0.25), ("compose", 0.60))
    _PLAIN_PLAN = (("compose", 1.0),)

    def __init__(self, emit, pip=True):
        """
        :param emit: 完成度回调（0~1），由界面侧换算成整批进度
        :param pip : 这条视频是否要走画中画（决定阶段权重表）
        """
        self._emit = emit
        self._plan = self._PIP_PLAN if pip else self._PLAIN_PLAN
        self._frac = {name: 0.0 for name, _ in self._plan}
        self._sent = -1.0

    def phase(self, name, frac):
        """记录某阶段的真实完成度；只增不减（并发下晚到的旧值不许把进度拽回去）"""
        if name not in self._frac:
            return
        self._frac[name] = max(self._frac[name], min(1.0, max(0.0, float(frac))))
        self._push()

    def count(self, name, done, total):
        """按个数记进度（副本数 / 批次数）；总数为 0 表示该阶段无事可做，直接算完成"""
        self.phase(name, (float(done) / total) if total else 1.0)

    def finish(self):
        """收尾：把还没报满的阶段补到 100%（这条视频确实已经处理完了）"""
        for name in self._frac:
            self._frac[name] = 1.0
        self._push()

    def _push(self):
        """完成度每变动 0.2% 才发一条消息，避免刷爆消息队列"""
        total = sum(weight for _, weight in self._plan)
        frac = sum(weight * self._frac[name] for name, weight in self._plan) / total
        if frac - self._sent < 0.002 and frac < 1.0:
            return
        self._sent = frac
        try:
            self._emit(frac)
        except Exception:
            pass


def _check_stopped(opts):
    """停止后不再启动新转码，也不把取消当作坏素材重新编码。"""
    event = opts.get("stop_event")
    if event is not None and event.is_set():
        raise CancelledError("处理已停止")


def process_one(src, out_dir, opts, registry, log, tag="", on_progress=None):
    """
    处理单个视频：准备画中画轨 → 一次合成出片 → 输出到 out_dir。

    异常策略：
        - 画中画素材生成失败（目录空、素材损坏等）只跳过画中画并如实说明原因，
          主流程照常出片，不因为一个小功能让整条视频作废。

    :param tag: 日志前缀（形如 "[x1.mp4]"）。多个视频并发时日志会交错，
                每行都带文件名才不会看混。
    :param on_progress: 真实完成度回调（0~1），由界面侧换算成整批进度；可空
    :return: 输出文件路径
    """
    _check_stopped(opts)
    t0 = time.time()
    # 预检阶段已读过该视频的信息就直接复用，不必再调一次 ffprobe
    info = (opts.get("infos") or {}).get(src) or probe_media(src)
    base, ext = os.path.splitext(os.path.basename(src))
    out_ext = ext.lower() if ext.lower() in KEEP_EXT else ".mp4"
    dst = os.path.join(out_dir, base + out_ext)
    # 防御：输出路径与源文件完全相同（例如有人把输出目录选成了源目录）时，
    # ffmpeg 会直接报"same as Input"并失败。这里加后缀避开，保证永远有输出。
    if os.path.abspath(dst) == os.path.abspath(src):
        dst = os.path.join(out_dir, base + "_已处理" + out_ext)

    # 真实进度：三个阶段各自用实测数字折算（见 VideoProgress），不猜时间
    prog = VideoProgress(on_progress, pip=bool(opts.get("pip_dir"))) if on_progress else None

    # 本文件的所有日志都带文件名前缀，便于在并发日志里对号入座
    def say(msg, keep=True):
        """
        keep=True  → 写日志：结果 / 异常 / 需要用户知道的说明
        keep=False → 只刷新窗口底部的进度值，不写日志：过程性提示，避免一行一行刷屏
        """
        if keep:
            log(f"{tag} {msg}" if tag else msg)

    # 先按「商品图概率」掷骰：未命中时这条视频整段不走商品图（见 _decide_product）。
    # 再随机固定首图与主图：目录里有多张时每条抽一张，抽定后本条不再变。
    opts = _decide_product(opts)
    opts = _pick_images(opts)
    if opts.get("product"):
        opts = _clamp_prod_start(opts, info, say)   # 起始帧超出视频长度时自动退到最后一帧
    picked = ""
    if opts.get("picked_cover"):
        picked = f"，首图 {opts['picked_cover']}"
    if opts.get("picked_product"):
        picked += f" · 主图 {opts['picked_product']}"
    else:
        picked += " · 本条不加商品图"
    say(f"开始处理（时长 {fmt_duration(info['duration'])}{picked}）")

    tmpdir, pip = None, None
    try:
        if opts.get("pip_dir"):
            tmpdir = tempfile.mkdtemp(prefix=".pip_", dir=out_dir)
            try:
                pip = build_pip_track(info, opts, tmpdir, registry,
                                      lambda msg: say(msg), prog)
            except Exception as e:
                _check_stopped(opts)
                say(f"画中画没做成，这条视频就不加画中画了（原因：{e}）")
                pip = None
        if prog is not None:
            # 副本与画中画都已就绪（没开画中画时这两个阶段不在计划里，调了也不影响）
            prog.phase("copy", 1.0)
            prog.phase("pip", 1.0)

        _check_stopped(opts)
        say("正在合成画面（最耗时的一步，请稍候）…", keep=False)
        cmd, tw, th = build_command(src, opts, info, dst, pip)
        # 合成阶段的进度由 ffmpeg 自己汇报（build_command 里加了 -progress pipe:1）
        _run(cmd, registry,
             on_progress=(lambda frac: prog.phase("compose", frac)) if prog else None,
             expected_dur=float(info.get("duration") or 0.0))
        if prog is not None:
            prog.finish()
    finally:
        if tmpdir:                     # 无论成功失败都清理临时片段，避免堆积
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 每个视频只留这两行结果：开始处理（含抽到的图）、完成（耗时 + 成品体积）
    size = 0
    try:
        size = os.path.getsize(dst)
    except OSError:
        pass
    say(f"✓ 完成，用时 {fmt_duration(time.time() - t0)}，输出 {fmt_bytes(size)}")
    return dst


# ============================================================================
# 八、图形界面
# ============================================================================

class App(tk.Tk):
    """主窗口。UI 只负责收集参数与展示进度，真正的重活在 worker 线程里做。"""

    def __init__(self):
        super().__init__()
        # 标题栏只留工具名：界面里不再放标题行（用户要求去掉，见 _build_ui 说明）
        self.title("短视频批处理工具")
        # 窗口尺寸按屏幕可用空间自适应，并居中偏上放置。
        # 写死 1080x880 在 13 寸屏上会被菜单栏 / Dock 切掉底部（用户反馈"没显示全"），
        # 所以取屏幕尺寸留出边距后再夹到目标范围内，最小尺寸也跟着一起收。
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        except Exception:
            sw, sh = 1440, 900
        width = max(860, min(1080, sw - 80))
        # 高度下限 680 是实测出来的：再矮日志区就不足 40px（见 test_video_frame_tool.py --ui）
        height = max(680, min(880, sh - 150))     # 150 ≈ 菜单栏 + Dock + 标题栏 + 边距
        self.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(24, (sh - height) // 3)}")
        self.minsize(min(860, width), min(680, height))

        # ---- 窗口图标（任务栏 / 标题栏 / Dock）----
        # 用内嵌 logo，不依赖外部文件；自定义 logo.png 会被优先采用，见 load_logo()
        # 注意：这里只设置窗口图标，界面内容区不显示任何 logo（用户明确要求去掉标题行）
        self.icon_image = None
        try:
            icon, _src = load_logo(256)
            if icon:
                self.icon_image = icon          # 保引用，否则图标会被回收
                self.iconphoto(True, icon)
        except Exception:
            pass

        # ---- 读取上次保存的配置 ------------------------------------------
        # 路径全部来自用户"上次的选择"，代码里不写死任何默认素材路径。
        # 配置文件位置由 user_config_dir() 按操作系统类型决定：
        #   Windows → %APPDATA%\video_frame_tool\settings.json
        #   macOS   → ~/Library/Application Support/video_frame_tool/settings.json
        #   Linux   → $XDG_CONFIG_HOME/video_frame_tool/settings.json
        self.settings = load_settings()
        sp, sc = self.settings["paths"], self.settings["pip"]
        sr, su = self.settings["random"], self.settings["run"]

        # ---- 路径（回填上次选择） ----
        self.cover_var = tk.StringVar(value=sp.get("cover", ""))
        self.product_var = tk.StringVar(value=sp.get("product", ""))
        self.dir_var = tk.StringVar(value=sp.get("video_dir", ""))
        self.pip_var = tk.StringVar(value=sp.get("pip_dir", ""))

        # ---- 画中画：总开关 + 几何 ----
        # 开关关掉时下面的参数全部不生效（也不生成低清副本、不拼画中画轨）
        self.pip_on = tk.BooleanVar(value=bool(sc.get("enabled", True)))
        self.pip_w = tk.StringVar(value=str(sc.get("w", "24")))
        self.pip_h = tk.StringVar(value=str(sc.get("h", "20")))
        self.pip_right = tk.StringVar(value=str(sc.get("right", "8")))
        self.pip_top = tk.StringVar(value=str(sc.get("top", "9")))
        self.pip_fill = tk.StringVar(value=sc.get("fill", "裁剪填满"))

        # ---- 画中画素材处理 ----
        self.pip_head = tk.StringVar(value=str(sc.get("head", "10")))
        self.pip_tail = tk.StringVar(value=str(sc.get("tail", "10")))
        self.pip_speed = tk.StringVar(value=str(sc.get("speed", "1.2")))

        # ---- 随机化 ----
        self.rnd_flip_h = tk.BooleanVar(value=bool(sr.get("flip_h", True)))
        self.rnd_zoom = tk.StringVar(value=str(sr.get("zoom", "1.06")))
        self.rnd_color = tk.BooleanVar(value=bool(sr.get("color", True)))
        self.rnd_jitter = tk.StringVar(value=str(sr.get("jitter", "3")))

        # ---- 运行参数 ----
        try:
            _workers = int(su.get("workers", DEFAULT_WORKERS))
        except Exception:
            _workers = DEFAULT_WORKERS
        self.workers_var = tk.IntVar(value=_workers)
        try:
            _prod_start = int(float(str(su.get("prod_start", PRODUCT_START_FRAME))))
        except Exception:
            _prod_start = PRODUCT_START_FRAME
        self.prod_start_var = tk.IntVar(value=_prod_start)    # 产品图从第几帧开始显示
        # 商品图显示概率（%）：每条视频独立掷骰；0 = 完全不叠加，100 = 每条都叠
        try:
            _prod_chance = int(float(str(su.get("prod_chance", PRODUCT_CHANCE))))
        except Exception:
            _prod_chance = PRODUCT_CHANCE
        self.prod_chance_var = tk.IntVar(value=max(0, min(100, _prod_chance)))
        self.preset_var = tk.StringVar(value=su.get("preset", DEFAULT_PRESET))
        # 硬件加速：老配置里没有这个字段，回落默认值（见 hw_decode_args）
        _hw = su.get("hwaccel", DEFAULT_HWACCEL)
        self.hwaccel_var = tk.StringVar(value=_hw if _hw in HWACCEL_MODES else DEFAULT_HWACCEL)
        self.out_var = tk.StringVar(
            value=os.path.join(sp["video_dir"], "out") if sp.get("video_dir") else "（未选择目录）")

        # ---- 运行时状态 ----
        self.msg_q = queue.Queue()             # 工作线程 → 主线程的消息队列
        self.registry = ProcRegistry()         # 在跑的 ffmpeg 进程
        self.warm_stop_event = threading.Event()  # 后台预热独立取消，前台开始时让路
        self.stop_event = threading.Event()    # 停止信号
        self.running = False
        self._poll_id = None                   # 消息泵定时器 id（关闭窗口时取消）

        # ---- 进度条状态：只装"真实发生过的工作量"，不做任何猜测 ----
        # 权重 = 该视频时长（耗时基本正比于时长，所以按秒数加权才准）；
        # 完成量 = 该视频内部各阶段的真实完成度（见 VideoProgress）。
        # 界面上的百分比 = 已完成工作量 / 全部工作量，只有真干完活才会往前走。
        self._v_weights = []                   # 每条视频的权重（时长秒；读不到时给 1）
        self._v_done = {}                      # 序号 -> 已完成工作量
        self._v_total = 0.0                    # 全部工作量
        self._v_dirty = False                  # 本轮是否有新的进度消息要落到进度条
        self._batch_done = 0                   # 预生成副本：已生成个数
        self._batch_total = 0                  # 预生成副本：总个数

        # ---- 系统资源监控（CPU / 内存 / 本进程占用，跨平台实现见 SystemMonitor） ----
        self.monitor = SystemMonitor()
        self._mon_id = None                    # 监控定时器 id（关闭窗口时取消）

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)   # 关窗前把选择落盘
        self._poll_id = self.after(120, self._poll_queue)   # 启动消息泵（记录 id 便于关闭时取消）
        self._mon_id = self.after(MONITOR_INTERVAL_MS, self._tick_monitor)   # 启动资源监控
        self._check_env()
        self.after_idle(self._present_window)

    def _present_window(self):
        """启动后将窗口带到前台；macOS 下也尝试激活 Python 应用本身。"""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(180, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass
        if IS_MACOS:
            try:
                subprocess.Popen(
                    ["osascript", "-e", 'tell application "Python" to activate'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass

    # ---------------------------------------------------- 配置持久化
    def _snapshot(self):
        """
        把界面上的当前选择打包成配置字典（"要记住的东西"都在这里定义）。
        以后新增界面参数时，记得同步补进这个字典，否则不会被记住。
        """
        try:
            workers = int(float(str(self.workers_var.get())))
        except Exception:
            workers = DEFAULT_WORKERS
        # 输入框被清空/填了非数字时 IntVar.get() 会抛 TclError。
        # 关窗时也要走这里，一旦抛出会导致窗口关不掉，所以必须兜住。
        try:
            prod_start = int(float(str(self.prod_start_var.get())))
        except Exception:
            prod_start = PRODUCT_START_FRAME
        try:
            prod_chance = int(float(str(self.prod_chance_var.get())))
        except Exception:
            prod_chance = PRODUCT_CHANCE
        prod_chance = max(0, min(100, prod_chance))
        return {
            "version": SETTINGS_VERSION,
            "paths": {
                "cover": self.cover_var.get().strip(),
                "product": self.product_var.get().strip(),
                "video_dir": self.dir_var.get().strip(),
                "pip_dir": self.pip_var.get().strip(),
            },
            "pip": {
                "enabled": bool(self.pip_on.get()),
                "w": self.pip_w.get(), "h": self.pip_h.get(),
                "right": self.pip_right.get(), "top": self.pip_top.get(),
                "fill": self.pip_fill.get(),
                "head": self.pip_head.get(), "tail": self.pip_tail.get(),
                "speed": self.pip_speed.get(),
                "small": bool(self.settings.get("pip", {}).get("small", True)),
            },
            "random": {
                "flip_h": bool(self.rnd_flip_h.get()),
                "zoom": self.rnd_zoom.get(), "color": bool(self.rnd_color.get()),
                "jitter": self.rnd_jitter.get(),
            },
            "run": {
                "workers": workers,
                "preset": self.preset_var.get(),
                "prod_start": prod_start,            # 产品图起始帧
                "prod_chance": prod_chance,          # 商品图显示概率（%）
                "hwaccel": self.hwaccel_var.get(),   # 硬件加速（自动/开启/关闭）
            },
        }

    def _save_settings(self):
        """把当前选择写入用户配置文件（写失败静默处理，不影响使用）"""
        self.settings = self._snapshot()
        save_settings(self.settings)

    def _on_close(self):
        """关闭窗口：先取消定时器、保存配置，再销毁窗口"""
        self._save_settings()
        self.warm_stop_event.set()
        self.stop_event.set()
        self.registry.kill_all()
        for attr in ("_poll_id", "_mon_id"):
            try:
                tid = getattr(self, attr, None)
                if tid:
                    self.after_cancel(tid)       # 防止窗口销毁后回调仍被触发
                    setattr(self, attr, None)
            except Exception:
                pass
        self.destroy()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        # 保留系统原生按钮、下拉框与勾选框，避免跨平台主题退化为凸起方框。
        style = ttk.Style(self)
        if PLATFORM == "macos" and "aqua" in style.theme_names():
            style.theme_use("aqua")
        # 字号一律跟随系统默认（TkDefaultFont），不手动放大——只在默认字号上加粗。
        # 手动指定字号会盖掉系统设置（用户开大字体时界面反而不跟随），也容易整体偏大。
        default_font = tkfont.nametofont("TkDefaultFont")
        family = default_font.actual("family")
        size = default_font.actual("size")
        style.configure("Section.TLabel", font=(family, size, "bold"))
        style.configure("Status.TLabel", font=(family, size, "bold"))
        style.configure("Muted.TLabel", foreground="#647080")

        pad = {"padx": 6, "pady": 2}
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=20, pady=12)
        root.columnconfigure(0, weight=1)

        def section(row, title):
            """用标题和留白分组，避免大面积描边压过实际内容。"""
            outer = ttk.Frame(root)
            outer.grid(row=row, column=0, sticky="nsew", pady=(0 if row == 0 else 16, 0))
            ttk.Label(outer, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 8))
            body = ttk.Frame(outer)
            body.pack(fill="both", expand=True)
            return body

        # ---------- 0. 路径区 ----------
        f_path = section(0, "素材路径")
        f_path.columnconfigure(1, weight=1)

        def path_row(r, label, var, cb):
            ttk.Label(f_path, text=label, width=10, anchor="w").grid(row=r, column=0, sticky="e", **pad)
            ttk.Entry(f_path, textvariable=var).grid(row=r, column=1, sticky="ew", **pad)
            ttk.Button(f_path, text="选择…", command=cb, width=9).grid(row=r, column=2, **pad)

        # 首图 / 主图都是选「目录」：每处理一个视频就从目录里随机各取一张，
        # 同一条视频全程只用同一对（见 _pick_images）。目录里只放一张图也完全没问题。
        path_row(0, "首图目录", self.cover_var,
                 lambda: self._pick_dir(self.cover_var, "选择首图目录（每条视频随机取一张）"))
        path_row(1, "主图目录", self.product_var,
                 lambda: self._pick_dir(self.product_var, "选择主图目录（每条视频随机取一张）"))
        # 商品图显示概率就挂在这一行：语义上它就是"主图目录"的开关。
        # 0 表示完全不叠加商品图（此时主图目录可以留空），100 表示每条视频必取一张。
        ttk.Label(f_path, text="显示概率", style="Muted.TLabel").grid(
            row=1, column=3, sticky="e", padx=(14, 4), pady=3)
        ttk.Spinbox(f_path, from_=0, to=100, width=5,
                    textvariable=self.prod_chance_var).grid(row=1, column=4, sticky="w", pady=3)
        ttk.Label(f_path, text="%（0=不叠加　100=每条必取一张；每条视频独立掷骰）",
                  style="Muted.TLabel").grid(row=1, column=5, sticky="w", padx=(4, 0), pady=3)
        path_row(2, "视频目录", self.dir_var, lambda: self._pick_dir(None, "选择视频目录"))
        path_row(3, "画中画目录", self.pip_var, lambda: self._pick_dir(self.pip_var, "选择画中画目录"))

        # ---------- 1. 画中画设置区 ----------
        f_pip = section(1, "画中画设置")
        pip_head = ttk.Frame(f_pip)
        pip_head.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Checkbutton(pip_head, text="启用画中画", variable=self.pip_on,
                        command=self._toggle_pip).pack(side="left")
        self.lbl_pip_hint = ttk.Label(pip_head, text="", style="Muted.TLabel")
        self.lbl_pip_hint.pack(side="left", padx=8)
        # 目录一改就刷新提示（选了目录 / 清空目录都要立刻反映出来）
        self.pip_var.trace_add("write", lambda *_: self._refresh_pip_hint())
        self._refresh_pip_hint()

        # 2.1 位置与尺寸
        r1 = ttk.Frame(f_pip)
        r1.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(r1, text="位置/尺寸：").pack(side="left")
        for text, var, tail in (("宽", self.pip_w, "%"), ("高", self.pip_h, "%"),
                                ("右距", self.pip_right, "%"), ("上距", self.pip_top, "%")):
            ttk.Label(r1, text=text).pack(side="left", padx=(8, 2))
            ttk.Entry(r1, width=4, textvariable=var).pack(side="left")
            ttk.Label(r1, text=tail).pack(side="left")
        ttk.Label(r1, text="   填充").pack(side="left")
        ttk.Combobox(r1, width=9, state="readonly", textvariable=self.pip_fill,
                     values=("裁剪填满", "完整显示")).pack(side="left", padx=(4, 0))

        # 2.2 时长处理
        r2 = ttk.Frame(f_pip)
        r2.pack(fill="x", padx=8, pady=2)
        ttk.Label(r2, text="时长处理：").pack(side="left")
        ttk.Label(r2, text="掐头").pack(side="left", padx=(8, 2))
        ttk.Entry(r2, width=5, textvariable=self.pip_head).pack(side="left")
        ttk.Label(r2, text="%").pack(side="left")
        ttk.Label(r2, text="去尾").pack(side="left", padx=(8, 2))
        ttk.Entry(r2, width=5, textvariable=self.pip_tail).pack(side="left")
        ttk.Label(r2, text="%").pack(side="left")
        ttk.Label(r2, text="   加速").pack(side="left", padx=(16, 2))
        ttk.Entry(r2, width=5, textvariable=self.pip_speed).pack(side="left")
        ttk.Label(r2, text="倍").pack(side="left")

        # 2.3 随机化（规避平台查重）
        r3 = ttk.Frame(f_pip)
        r3.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Label(r3, text="随机化　：").pack(side="left")
        ttk.Checkbutton(r3, text="水平翻转", variable=self.rnd_flip_h).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(r3, text="随机调色", variable=self.rnd_color).pack(side="left", padx=(8, 0))
        ttk.Label(r3, text="随机缩放上限").pack(side="left", padx=(8, 2))
        ttk.Entry(r3, width=5, textvariable=self.rnd_zoom).pack(side="left")
        ttk.Label(r3, text="　位置抖动±").pack(side="left")
        ttk.Entry(r3, width=4, textvariable=self.rnd_jitter).pack(side="left")
        ttk.Label(r3, text="px").pack(side="left")

        cache_bar = ttk.Frame(f_pip)
        cache_bar.pack(fill="x", padx=8, pady=(8, 0))
        self.btn_prep = ttk.Button(cache_bar, text="预生成低清副本", command=self._prepare_small)
        self.btn_prep.pack(side="left")
        ttk.Button(cache_bar, text="打开副本目录", command=self._open_small).pack(side="left", padx=8)
        ttk.Label(cache_bar, text="副本自动复用，无需每次生成", style="Muted.TLabel").pack(side="left", padx=8)

        # ---------- 2. 运行参数区 ----------
        f_run = section(2, "输出设置")
        run_options = ttk.Frame(f_run)
        run_options.pack(fill="x")
        ttk.Label(run_options, text="同时处理").pack(side="left")
        ttk.Spinbox(run_options, from_=1, to=32, width=5,
                    textvariable=self.workers_var).pack(side="left", padx=(6, 18))
        ttk.Label(run_options, text="编码速度").pack(side="left")
        ttk.Combobox(run_options, width=7, state="readonly", textvariable=self.preset_var,
                     values=tuple(PRESET_MAP.keys())).pack(side="left", padx=(6, 16))
        # 产品图起始帧：默认第 60 帧（填 0 表示从第一帧就显示）
        ttk.Label(run_options, text="主图从第").pack(side="left")
        ttk.Spinbox(run_options, from_=0, to=PRODUCT_START_MAX, width=6,
                    textvariable=self.prod_start_var).pack(side="left", padx=(4, 2))
        ttk.Label(run_options, text="帧开始显示").pack(side="left", padx=(0, 16))

        ttk.Label(run_options, text="硬件解码").pack(side="left", padx=(8, 6))
        ttk.Combobox(run_options, width=6, state="readonly", textvariable=self.hwaccel_var,
                     values=HWACCEL_MODES).pack(side="left")
        output_row = ttk.Frame(f_run)
        output_row.pack(fill="x", pady=(12, 0))
        ttk.Label(output_row, text="输出目录", style="Muted.TLabel").pack(side="left", padx=(0, 12))
        ttk.Entry(output_row, textvariable=self.out_var, state="readonly").pack(side="left", fill="x", expand=True)
        self.btn_open = ttk.Button(output_row, text="打开目录", command=self._open_out)
        self.btn_open.pack(side="left", padx=(8, 0))

        bar = ttk.Frame(root)
        bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.btn_start = ttk.Button(bar, text="开始处理", command=self._start, default="active", width=12)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)

        # 系统资源监控挤在按钮这一行的右侧（用户要求：不要为它单独占一整行）
        f_mon = ttk.Frame(bar)
        f_mon.pack(side="right")
        ttk.Label(f_mon, text="整机 CPU", style="Muted.TLabel").pack(side="left")
        self.mon_cpu = ttk.Progressbar(f_mon, mode="determinate", maximum=100, length=60)
        self.mon_cpu.pack(side="left", padx=(4, 4))
        self.mon_cpu_text = ttk.Label(f_mon, text="—", width=5, anchor="e")
        self.mon_cpu_text.pack(side="left")

        ttk.Label(f_mon, text="整机内存", style="Muted.TLabel").pack(side="left", padx=(14, 0))
        self.mon_mem = ttk.Progressbar(f_mon, mode="determinate", maximum=100, length=60)
        self.mon_mem.pack(side="left", padx=(4, 4))
        # 内存这串长度会随数值变（"9.9/24.0 GiB" → "19.5/128.0 GiB"），
        # 所以**不能给固定字符宽**——宽了会把后半段直接裁掉（用户反馈过"内存大小有变被盖着了"）。
        # 不设 width 让它按内容自适应；整块是靠右摆放，变宽只会往左推，不会挤到按钮。
        self.mon_mem_text = ttk.Label(f_mon, text="—", anchor="e")
        self.mon_mem_text.pack(side="left")

        # ---------- 3. 总进度 ----------
        # 一整行只放两样东西：长进度条 + 右边一个大概的进度值。
        # 细节（第几个视频、正在做什么、成功失败各几个）一律写日志，不在这里堆
        # ——用户明确要求这个位置"只用显示一个大概的进度值"。
        f_prog = ttk.Frame(root)
        f_prog.grid(row=4, column=0, sticky="ew", pady=(10, 8))
        f_prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(f_prog, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_text = ttk.Label(f_prog, text="就绪", width=12, anchor="e",
                                       style="Status.TLabel")
        self.progress_text.grid(row=0, column=1, sticky="e", padx=(12, 0))

        f_log = section(5, "处理结果与异常")
        root.rowconfigure(5, weight=1)   # 结果区只用剩余空间，主进度保持可见
        f_log.columnconfigure(0, weight=1)
        f_log.rowconfigure(0, weight=1)
        # 结果区使用系统字体与自动换行，避免错误信息横向截断。
        self.log_box = tk.Text(f_log, height=5, wrap="word", font=(mono_font_family(), 11), relief="flat", borderwidth=0,
                               padx=8, pady=8)
        self.log_box.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f_log, orient="vertical", command=self.log_box.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_box.configure(yscrollcommand=sb.set, state="disabled")

    def _check_env(self):
        """启动时检查运行环境：没有 ffmpeg 就禁用开始按钮，并给出各平台的安装办法"""
        if not FFMPEG:
            self._log("✗ 没有找到 ffmpeg，暂时无法处理视频。请先安装：")
            self._log("     Windows：winget install Gyan.FFmpeg   或  scoop install ffmpeg")
            self._log("     macOS  ：brew install ffmpeg")
            self._log("     Linux  ：sudo apt install ffmpeg")
            self._log("     通用兜底：pip install imageio-ffmpeg")
            self.btn_start.configure(state="disabled")
            self.btn_prep.configure(state="disabled")
            return

        stale = [label for label, path in (
            ("首图目录", self.cover_var.get()), ("主图目录", self.product_var.get()),
            ("视频目录", self.dir_var.get()), ("画中画目录", self.pip_var.get()))
            if path and not os.path.isdir(path)]
        if stale:
            self._log("请重新选择不存在的路径：" + "、".join(stale))

    # ---------------------------------------------------------- 选择/打开
    # 每次选择完成后立刻落盘，"上次选择"就是这样被记住的。
    def _toggle_pip(self):
        """画中画总开关：关掉时把"预生成低清副本"一并禁用（副本只服务于画中画）"""
        self.btn_prep.configure(state="normal" if self.pip_on.get() else "disabled")
        self._refresh_pip_hint()

    def _refresh_pip_hint(self):
        """刷新画中画开关右侧的提示，让"当前到底会不会拼画中画"一眼可见"""
        if not hasattr(self, "lbl_pip_hint"):
            return
        if not self.pip_on.get():
            text = "已关闭：不拼画中画，也不会生成低清副本"
        elif not self.pip_var.get().strip():
            text = "已启用，但还没选画中画目录"
        else:
            text = "已启用"
        self.lbl_pip_hint.configure(text=text)

    def _pick_dir(self, var=None, title="选择目录"):
        p = filedialog.askdirectory(title=title)
        if p:
            (var or self.dir_var).set(p)
            if var is None:
                self.out_var.set(os.path.join(p, "out"))
            self._save_settings()

    def _open_out(self):
        """打开输出目录（由平台适配层选择 open / xdg-open / startfile）"""
        d = os.path.join(self.dir_var.get().strip(), "out") if self.dir_var.get().strip() else ""
        if not d:
            messagebox.showinfo("提示", "请先选择视频目录")
        elif not os.path.isdir(d):
            messagebox.showinfo("提示", "输出目录还不存在")
        elif not open_folder(d):
            messagebox.showwarning("提示", f"无法自动打开目录，请手动前往：\n{d}")

    def _open_small(self):
        """打开当前小视频素材目录下的真实副本目录，不创建空缓存。"""
        folder = self.pip_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("副本目录", "请先选择有效的画中画目录。")
            return
        path = small_dir_of(folder)
        if not os.path.isdir(path):
            messagebox.showinfo("副本尚未生成", f"生成后将保存在：\n{path}")
        elif not open_folder(path):
            messagebox.showwarning("副本目录", f"无法自动打开，请手动前往：\n{path}")

    def _clear_log(self):
        """每批只展示本次结果，避免历史消息挤占进度区域。"""
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # -------------------------------------------------- 预生成低清副本
    def _prepare_small(self):
        """
        手动把画中画素材的低清副本提前建好（副本会长期保留，下次处理直接复用）。

        日常其实不用点：每次处理完都会在后台慢慢补齐整池。这个按钮是给
        "换了素材目录 / 调大了画中画尺寸"之后用的，免得下次正式跑的时候干等。
        """
        if self.running:
            return
        if not self.pip_on.get():
            messagebox.showinfo("画中画已关闭",
                                "没有启用画中画，不需要低清副本。\n"
                                "要预生成请先勾选「启用画中画」。")
            return
        pip_dir = self.pip_var.get().strip()
        vdir = self.dir_var.get().strip()
        if not pip_dir or not os.path.isdir(pip_dir):
            messagebox.showwarning("路径无效", f"画中画目录不存在：{pip_dir or '（未选择）'}")
            return
        # 副本尺寸要按主视频算，所以需要视频目录里至少有一个视频
        files = _list_videos(vdir) if vdir and os.path.isdir(vdir) else []
        if not files:
            messagebox.showwarning("路径无效",
                                   "请先选择视频目录（副本尺寸要按视频画面大小来算）")
            return

        opts = self._collect_opts()
        self.warm_stop_event.set()
        self._clear_log()
        self.running = True
        self.stop_event.clear()
        self.btn_start.configure(state="disabled", text="处理中…")
        self.btn_prep.configure(state="disabled")
        self.btn_stop.configure(state="normal", text="停止")
        self._set_progress_text("准备中…")
        # 预生成走的是"副本个数"这条真实计数，与主流程的时长权重互不干扰
        self._v_weights, self._v_done, self._v_total = [], {}, 0.0
        self._batch_done = self._batch_total = 0
        self.progress.configure(maximum=100, value=0)
        self._log(f"预生成低清副本：视频目录 {vdir}，画中画素材目录 {opts['pip_dir']}")
        threading.Thread(target=self._small_worker,
                         args=(vdir, files, opts), daemon=True).start()

    def _small_worker(self, vdir, files, opts):
        """后台线程：读出最大视频的尺寸 → 按它算副本短边 → 把整池副本建齐"""
        t0 = time.time()
        try:
            # 尺寸按"最大的一条视频"算：短边宁可大一点，算小了下次还得重建
            biggest = None
            with ThreadPoolExecutor(max_workers=min(8, len(files))) as ex:
                for info in ex.map(_safe_probe, files):
                    if not info:
                        continue
                    if biggest is None or (_small_short_side(info["width"], info["height"], opts)
                                           > _small_short_side(biggest["width"], biggest["height"], opts)):
                        biggest = info
            if biggest is None:
                raise RuntimeError("视频目录里没有能读出来的视频")
            short = _small_short_side(biggest["width"], biggest["height"], opts)
            pool = scan_pip_pool(opts["pip_dir"])
            if not pool:
                raise RuntimeError(f"画中画目录里没有可用素材：{opts['pip_dir']}")

            self.msg_q.put(("total", len(pool)))

            def tick(n, m):
                # 进度只走进度条与右侧进度值，不刷日志（这一格用户要求"只看一个大概的数"）
                self.msg_q.put(("progress", (n, m)))

            got = prepare_small_pool(pool, short, None, self.stop_event,
                                     hw=opts.get("hwaccel"), progress_cb=tick) or {}
            have2 = len(got)
            self.msg_q.put(("small_done",
                            (have2, len(pool), time.time() - t0)))
        except Exception as e:
            self.msg_q.put(("small_error", str(e)))

    def _finish_small(self, payload):
        """预生成结束（成功）后的收尾"""
        have, total, cost = payload
        self.running = False
        self.btn_start.configure(state="normal", text="开始处理")
        self.btn_prep.configure(state="normal")
        self.btn_stop.configure(state="disabled", text="已完成")
        self._draw_progress(self._percent(have, total))
        state = "已停止" if self.stop_event.is_set() else ("已完成" if have == total else "部分完成")
        self._set_progress_text(f"副本 {have}/{total}")
        self._log(f"—— 预生成{state}：低清副本 {have}/{total} 个就绪，"
                  f"用时 {fmt_duration(cost)} ——")

    def _finish_small_error(self, msg):
        """预生成出错时的收尾（不弹窗打断，只写日志并恢复按钮）"""
        self.running = False
        self.btn_start.configure(state="normal", text="开始处理")
        self.btn_prep.configure(state="normal")
        self.btn_stop.configure(state="disabled", text="停止")
        self._draw_progress(0.0)
        self._set_progress_text("预生成失败")
        self._log(f"✗ 预生成低清副本失败：{msg}")

    # ------------------------------------------------------------ 日志
    def _log(self, text):
        """立即写日志（仅启动阶段/单条消息使用）"""
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self._trim_log()
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_batch(self, lines):
        """批量写日志：一次性插入多行，减少 Text 重排次数（性能关键）"""
        if not lines:
            return
        self.log_box.configure(state="normal")
        self.log_box.insert("end", "\n".join(lines) + "\n")
        self._trim_log()
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _trim_log(self):
        """限制日志行数，超出丢弃最老的行，防止长时间运行后界面变卡"""
        try:
            total = int(self.log_box.index("end-1c").split(".")[0])
            if total > LOG_MAX_LINES:
                self.log_box.delete("1.0", f"{total - LOG_MAX_LINES}.0")
        except Exception:
            pass

    # -------------------------------------------------------- 消息泵
    def _poll_queue(self):
        """
        主线程消息泵：每 120ms 收一次工作线程的消息。

        进度条**只吃真实数字**，不做任何估算：
          · 主流程   —— ("vtotal") 给出每条视频的权重（时长），
                        ("vprogress") 给出"这条视频已完成的工作量"，
                        百分比 = 已完成工作量 / 全部工作量（VideoProgress 算出来的）
          · 预生成副本 —— ("total") + ("progress", 已完成个数, 总个数)，本身就是真实计数
        日志仍然合批插入，界面不会被高频消息拖慢。
        """
        logs, done, small, small_err = [], None, None, None
        small_count, v_dirty = None, False
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    logs.append(payload)
                elif kind == "vtotal":
                    # 权重按视频时长分配：耗时基本正比于时长，按秒数加权才准
                    self._v_weights, self._v_total = payload
                    self._v_done = {}
                    self.progress.configure(mode="determinate", maximum=100, value=0)
                elif kind == "vprogress":
                    idx, work = payload
                    self._v_done[idx] = max(self._v_done.get(idx, 0.0), float(work))
                    v_dirty = True
                elif kind == "total":
                    self._batch_total = payload
                    self._batch_done = 0
                    self.progress.configure(mode="determinate", maximum=100, value=0)
                elif kind == "progress":
                    self._batch_done = payload[0]
                    self._batch_total = payload[1]
                    small_count = self._percent(payload[0], payload[1])
                elif kind == "error":
                    self._finish_error(payload)
                elif kind == "done":
                    done = payload
                elif kind == "small_done":
                    small = payload
                elif kind == "small_error":
                    small_err = payload
        except queue.Empty:
            pass
        if v_dirty:
            self._draw_progress(self._v_percent())
        elif small_count is not None:
            self._draw_progress(small_count)
        self._log_batch(logs)
        if done:
            self._finish(done)
        if small:
            self._finish_small(small)
        if small_err:
            self._finish_small_error(small_err)
        self._poll_id = self.after(120, self._poll_queue)

    # -------------------------------------------------------- 进度显示
    @staticmethod
    def _percent(done, total):
        """已完成 / 总数 → 百分比（分母为 0 时按 0 算，绝不抛异常）"""
        return 100.0 * float(done) / float(total) if total else 0.0

    def _v_percent(self):
        """主流程的真实百分比：已完成工作量 / 全部工作量"""
        return self._percent(sum(self._v_done.values()), self._v_total)

    def _draw_progress(self, percent):
        """
        进度条 + 右侧那个大概的进度值。

        数值本身只增不减（VideoProgress 逐条只报增量、已完成量只累加），
        所以这里不会再出现"进度条往回缩"的观感问题。
        """
        pct = max(0.0, min(100.0, float(percent)))
        self.progress.configure(mode="determinate", maximum=100, value=pct)
        self.progress_text.configure(text=f"{pct:.0f}%")

    def _set_progress_text(self, text):
        """右侧进度值区在不跑任务时显示一个短状态词（细节一律写日志）"""
        self.progress_text.configure(text=text)

    # ------------------------------------------------------------ 参数
    def _num(self, var, default, lo, hi):
        """读取数值输入框，非法或越界时回落到默认值/边界（永不抛异常）"""
        try:
            v = float(str(var.get()).strip())
        except Exception:
            v = default
        return max(lo, min(hi, v))

    def _collect_opts(self):
        """把界面上的控件值汇总成选项字典（工作线程只认这个字典）"""
        try:
            workers = int(float(str(self.workers_var.get())))
        except Exception:
            workers = DEFAULT_WORKERS          # Spinbox 被清空等异常输入时回落默认值
        workers = max(1, min(32, workers or DEFAULT_WORKERS))
        return {
            # 路径与图片
            "cover": self.cover_var.get().strip(),
            "product": self.product_var.get().strip(),
            "prod_size": None,                        # 由 _start 填充
            "prod_start": int(self._num(self.prod_start_var, PRODUCT_START_FRAME,
                                        0, PRODUCT_START_MAX)),   # 产品图从第几帧开始显示
            "prod_chance": int(self._num(self.prod_chance_var, PRODUCT_CHANCE,
                                         0, 100)),   # 商品图显示概率（%，每条视频独立掷骰）
            # 画中画：总开关关掉时一律当"没启用"，下面的几何参数留着以备下次打开
            "pip_on": bool(self.pip_on.get()),
            "pip_dir": (self.pip_var.get().strip() or None) if self.pip_on.get() else None,
            "pip_w": self._num(self.pip_w, 24, 2, 100) / 100.0,
            "pip_h": self._num(self.pip_h, 20, 2, 100) / 100.0,
            "pip_right": self._num(self.pip_right, 8, 0, 90) / 100.0,
            "pip_top": self._num(self.pip_top, 9, 0, 90) / 100.0,
            "pip_fill": "crop" if self.pip_fill.get() == "裁剪填满" else "pad",
            "pip_head": self._num(self.pip_head, 10, 0, 45) / 100.0,
            "pip_tail": self._num(self.pip_tail, 10, 0, 45) / 100.0,
            "pip_speed": self._num(self.pip_speed, 1.2, 0.25, 4.0),
            # 随机化
            "rnd_flip_h": self.rnd_flip_h.get(),
            "rnd_zoom": self._num(self.rnd_zoom, 1.0, 1.0, 1.30),
            "rnd_color": self.rnd_color.get(),
            "rnd_jitter": int(self._num(self.rnd_jitter, 0, 0, 20)),
            # 运行
            # 兜底取"默认档"的映射值，不要写死 veryfast —— 写死会让默认档形同虚设
            "preset": PRESET_MAP.get(self.preset_var.get(), PRESET_MAP[DEFAULT_PRESET]),
            "workers": workers,
            # 硬件加速：只作用于解码（见 hw_decode_args 的实测表），编码始终用 libx264
            "hwaccel": self.hwaccel_var.get(),
            # 编码线程配额：并发 N 路时每路只分 1/ N 的核，避免整机卡死
            "threads": max(1, CPU_COUNT // workers),
            # 素材加速副本：把画中画素材预先降成小尺寸再拼接（见 prepare_small_pool）
            "pip_small": bool(self.settings.get("pip", {}).get("small", True)),
            # 停止信号：素材副本预处理同样要能被打断
            "stop_event": self.stop_event,
        }

    # ------------------------------------------------------------ 主流程
    def _start(self):
        """点击"开始处理"：校验参数 → 收集文件 → 起 worker 线程"""
        if self.running:
            return
        opts = self._collect_opts()
        vdir = self.dir_var.get().strip()

        # ---- 参数校验 ----
        if not vdir or not os.path.isdir(vdir):
            messagebox.showwarning("路径无效", f"视频目录不存在：{vdir or '（未选择）'}")
            return
        # 首图 / 主图允许给目录（每条视频从里面随机取一张），也兼容以前的单个文件。
        # 主图只在"商品图概率 > 0"时才要求选：概率 0 就是明确不要商品图，
        # 这时还逼用户选一个主图目录没有意义。
        opts["cover_files"] = list_images(opts["cover"])
        if not opts["cover_files"]:
            messagebox.showwarning(
                "缺少参数",
                f"首图目录里没有找到可用的图片：{opts['cover'] or '（未选择）'}")
            return
        opts["product_files"] = []
        opts["prod_size"] = None
        if opts["prod_chance"] > 0:
            opts["product_files"] = list_images(opts["product"])
            if not opts["product_files"]:
                messagebox.showwarning(
                    "缺少参数",
                    f"主图目录里没有找到可用的图片：{opts['product'] or '（未选择）'}\n"
                    f"（当前商品图概率是 {opts['prod_chance']}%；"
                    f"完全不要商品图请把概率改成 0）")
                return
            try:
                opts["prod_size"] = probe_image_size(opts["product_files"][0])
            except Exception as e:
                messagebox.showerror("主图读取失败", str(e))
                return
        if opts["pip_on"] and not opts["pip_dir"]:
            messagebox.showwarning(
                "缺少参数",
                "已勾选「启用画中画」，请先选择画中画目录；\n不需要画中画就取消勾选。")
            return
        if opts["pip_dir"] and not os.path.isdir(opts["pip_dir"]):
            messagebox.showwarning("路径无效", f"画中画目录不存在：{opts['pip_dir']}")
            return

        # ---- 立刻切到运行态：先给用户反馈，再去扫目录（扫描可能耗时） ----
        self.warm_stop_event.set()
        self._clear_log()
        self.running = True
        self.stop_event.clear()
        self.btn_start.configure(state="disabled", text="处理中…")
        self.btn_prep.configure(state="disabled")
        self.btn_stop.configure(state="normal", text="停止")
        self._set_progress_text("准备中…")

        out_dir = os.path.join(vdir, "out")
        try:
            os.makedirs(out_dir, exist_ok=True)
            files = _list_videos(vdir)
        except Exception as e:
            self._to_idle()
            messagebox.showerror("无法创建输出目录", str(e))
            return
        if not files:
            self._to_idle()
            messagebox.showinfo("提示", "这个目录里没有找到视频文件")
            return

        # ---- 按"实际要处理的视频数"分配算力（关键性能设置） ----
        # 只处理 2 个视频却把并发设成 5 时，若按并发数分配线程，每个视频只能分到
        # CPU核数/5 个线程，会有大半核心闲置、整体慢好几倍。这里按实际使用到的
        # 并发数分配：每个视频拿到 CPU核数/实际并发 个线程。
        effective = max(1, min(opts["workers"], len(files)))
        opts["threads"] = max(1, CPU_COUNT // effective)
        # 画中画的素材片段也并行编码：并行度同样按实际并发数折算，避免抢满核心
        opts["pip_parallel"] = max(1, min(4, (CPU_COUNT // effective) // 2))

        # ---- 记录本次全部选择（下一次启动自动回填） ----
        self._save_settings()

        # ---- 进度条归零 + 把本次任务写清楚（进度条只吃真实数字，规模先摆出来） ----
        self.progress.configure(mode="determinate", value=0, maximum=100)
        self._v_weights, self._v_done, self._v_total = [], {}, 0.0
        self._batch_done = self._batch_total = 0
        self._set_progress_text("准备中…")

        self._log(f"开始处理：{len(files)} 个视频，同时处理 {effective} 个"
                  f"（每个分到 {opts['threads']} 个编码线程）")
        self._log(f"视频目录：{vdir}")
        self._log(f"输出目录：{out_dir}")
        self._log(f"首图：从「{os.path.basename(opts['cover'])}」的 "
                  f"{len(opts['cover_files'])} 张里随机取一张")
        if opts["prod_chance"] <= 0:
            self._log("商品图：不叠加（概率 0%）")
        else:
            self._log(f"商品图：{opts['prod_chance']}% 概率叠加（每条视频独立掷骰）；"
                      f"命中时从「{os.path.basename(opts['product'])}」的 "
                      f"{len(opts['product_files'])} 张里随机取一张，"
                      f"从第 {opts['prod_start']} 帧起显示（同一条视频内固定不变）")
        if opts.get("pip_dir"):
            self._log(f"画中画：素材来自「{os.path.basename(opts['pip_dir'])}」，"
                      f"占画面 {opts['pip_w'] * 100:.0f}% × {opts['pip_h'] * 100:.0f}%（右上角），"
                      f"每段掐头去尾 {opts['pip_head'] * 100:.0f}% / {opts['pip_tail'] * 100:.0f}%、"
                      f"加速 {opts['pip_speed']} 倍"
                      + ("、素材不重复" if opts.get("pip_small") else ""))
        else:
            self._log("画中画：已关闭（不拼画中画，也不生成低清副本）")
        self._log("抗查重随机化：" + "、".join(
            [f"缩放 ≤{opts['rnd_zoom']}x"] +
            (["水平翻转"] if opts["rnd_flip_h"] else []) +
            (["随机调色"] if opts["rnd_color"] else []) +
            ([f"位置抖动 ±{opts['rnd_jitter']}px"] if opts["rnd_jitter"] else [])))
        self._log(f"编码：{self.preset_var.get()}（libx264 / CRF {CRF}，"
                  f"硬件解码 {opts['hwaccel']}）")

        # 本条日志之后，每个视频各写两行：开始处理 + 完成（见 process_one）
        self._log("—— 开始处理，进度条按真实完成量推进 ——")

        threading.Thread(target=self._worker, args=(files, out_dir, opts), daemon=True).start()

    def _to_idle(self):
        """把界面恢复成"可以再次开始"的状态（参数校验失败 / 目录里没视频时用）"""
        self.running = False
        self.btn_start.configure(state="normal", text="开始处理")
        self.btn_prep.configure(state="normal")
        self.btn_stop.configure(state="disabled", text="停止")
        self._set_progress_text("就绪")

    def _worker(self, files, out_dir, opts):
        """
        工作线程：并发处理所有视频。

        - 每个视频的日志都带「[文件名]」前缀，并发交错时也能一眼看出是哪个文件
        - 单个文件失败只记一行，不影响其它文件
        - 收到停止信号后不再启动新任务（正在跑的 ffmpeg 已被终止，会很快退出）

        进度：每条视频按**时长**分配权重（耗时基本正比于时长，按个数平均分
        会让长视频严重欠报），再用 VideoProgress 报回来它自己的真实完成度。
        所以百分比是"已完成工作量 / 全部工作量"，不是"第几个 / 一共几个"。
        """
        ok = fail = 0
        out_size = 0
        total = len(files)
        t0 = time.time()

        # ---- 阶段 0：先把所有视频的信息读出来（几十毫秒级），再动手干重活 ----
        # 顺序很重要：读信息很轻，拼画中画 / 合成成片才吃 CPU。
        # 先把"这次要做多少活"摆到用户面前，点完开始就不会一片空白地干等。
        infos = {}
        try:
            with ThreadPoolExecutor(max_workers=min(8, total)) as ex:
                for path, res in zip(files, ex.map(_safe_probe, files)):
                    if res:
                        infos[path] = res
        except Exception:
            pass
        opts["infos"] = infos          # 下游直接复用，省掉重复探测

        # 权重=时长；读不到时长的按 1 秒算，保证它至少也算一份工作量
        weights = [float((infos.get(f) or {}).get("duration") or 0.0) or 1.0 for f in files]
        self.msg_q.put(("vtotal", (weights, sum(weights))))
        if infos:
            tt = sum(i["duration"] for i in infos.values())
            self._log_q(f"共 {len(infos)} 个视频，总时长 {fmt_duration(tt)}"
                        f"（平均 {fmt_duration(tt / len(infos))}）")

        def reporter(idx):
            """把第 idx 条视频的真实完成度换算成"整批已完成的工作量"丢进队列"""
            def emit(frac):
                self.msg_q.put(("vprogress", (idx, weights[idx] * frac)))
            return emit

        def run_one(idx, path):
            _check_stopped(opts)
            return process_one(path, out_dir, opts, self.registry, self._log_q,
                               f"[{os.path.basename(path)}]", reporter(idx))

        with ThreadPoolExecutor(max_workers=max(1, min(opts["workers"], total))) as ex:
            futures = {ex.submit(run_one, i, f): (i, f) for i, f in enumerate(files)}
            for fut in as_completed(futures):
                idx, path = futures[fut]
                name = os.path.basename(path)
                try:
                    out = fut.result()
                    ok += 1
                    try:
                        out_size += os.path.getsize(out)
                    except OSError:
                        pass
                except CancelledError:
                    continue
                except Exception as e:
                    if self.stop_event.is_set():
                        continue
                    fail += 1
                    self._log_q(f"[{name}] ✗ 失败：{e}")
                # 这条视频不会再占时间了：不论成功失败，都按满额计入真实进度
                self.msg_q.put(("vprogress", (idx, weights[idx])))
        elapsed = time.time() - t0

        # ---- 收尾：趁用户看结果的时候，后台把整池素材的低清副本补齐 ----
        # 下次再处理（哪怕抽到完全不同的素材）就几乎不用等了。
        # 尺寸按"最大的一条视频"算：副本短边是"只大不小"更安全，算小了会在下次被重建。
        if (not self.stop_event.is_set() and opts.get("pip_small", True)
                and opts.get("pip_dir") and infos):
            biggest = max(infos.values(),
                          key=lambda i: _small_short_side(i["width"], i["height"], opts))
            self.warm_stop_event = threading.Event()
            warm_opts = dict(opts, stop_event=self.warm_stop_event)
            _warm_small_pool_async(opts["pip_dir"], biggest, warm_opts)
        # 先登记后台任务再通知 UI 解锁，避免下一批启动时漏掉预热取消信号。
        self.msg_q.put(("done", (ok, fail, elapsed, out_size)))

    def _log_q(self, text):
        """供工作线程调用：把日志丢进队列，由主线程统一渲染"""
        self.msg_q.put(("log", text))

    def _stop(self):
        """停止：置位停止信号 + 终止所有在跑的 ffmpeg"""
        if not self.running:
            return
        self.stop_event.set()
        self.registry.kill_all()
        self.btn_start.configure(text="正在停止…", state="disabled")
        self.btn_stop.configure(state="disabled", text="停止")
        self._set_progress_text("正在停止…")

    def _finish(self, payload):
        """全部任务结束后的收尾"""
        ok, fail, cost, out_size = payload
        self.running = False
        self.btn_start.configure(state="normal", text="开始处理")
        self.btn_prep.configure(state="normal")
        self.btn_stop.configure(state="disabled", text="已完成")
        # 进度条收在真实完成度上：全部做完就是 100%，中途停止就停在停下的位置
        self._draw_progress(100.0 if not self.stop_event.is_set() else self._v_percent())
        state = "已停止" if self.stop_event.is_set() else "全部完成"
        self._set_progress_text(state)
        done = ok + fail
        self._log(f"—— {state}：成功 {ok} 个，失败 {fail} 个，"
                  f"总用时 {fmt_duration(cost)}"
                  + (f"（平均每个 {fmt_duration(cost / done)}）" if done else "")
                  + f"，输出合计 {fmt_bytes(out_size)} ——")
        if ok:
            self._log("成品都在输出目录里，可以点「打开目录」查看。")

    # ------------------------------------------------------ 系统资源监控
    def _tick_monitor(self):
        """
        每秒刷新一次底部的 CPU / 内存读数。

        采样在各平台都是微秒级调用（macOS 走 mach 接口，Linux 读 /proc，Windows 调 Win32 API），
        不会拖慢界面；任何一项取不到就显示 "—"，绝不因为监控本身影响主流程。
        """
        try:
            s = self.monitor.sample()

            cpu = s["cpu"]
            if cpu is None:
                self.mon_cpu["value"] = 0
                self.mon_cpu_text.configure(text="—")     # 首次采样只有快照，下一秒才有数
            else:
                self.mon_cpu["value"] = cpu
                self.mon_cpu_text.configure(text=f"{cpu:.0f}%")

            used, total = s["mem_used"], s["mem_total"]
            if used is None or not total:
                self.mon_mem["value"] = 0
                self.mon_mem_text.configure(text="—")
            else:
                pct = 100.0 * used / total
                self.mon_mem["value"] = pct
                self.mon_mem_text.configure(text=f"{pct:.0f}%　{used / 1024**3:.1f}/{total / 1024**3:.1f} GiB")

        except Exception:
            pass                                        # 监控出问题也不能影响处理流程
        finally:
            try:
                self._mon_id = self.after(MONITOR_INTERVAL_MS, self._tick_monitor)
            except Exception:
                self._mon_id = None

    def _finish_error(self, msg):
        """启动阶段异常的统一处理"""
        self.running = False
        self.btn_start.configure(state="normal", text="开始处理")
        self.btn_prep.configure(state="normal")
        self.btn_stop.configure(state="disabled", text="停止")
        messagebox.showerror("错误", msg)


def main():
    enable_high_dpi()          # Windows 高分屏适配（其它平台为空操作）
    if not FFMPEG:
        print("未找到 ffmpeg。请按平台安装：")
        print("  Windows : winget install Gyan.FFmpeg   或 scoop install ffmpeg")
        print("  macOS   : brew install ffmpeg")
        print("  Linux   : sudo apt install ffmpeg")
        print("  通用兜底: pip install imageio-ffmpeg")
        sys.exit(1)
    App().mainloop()


if __name__ == "__main__":
    main()
