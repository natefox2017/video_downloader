"""短视频批处理工具（GUI）。

按功能拆分成多个子模块；本文件把所有顶层名字重新导出，
因此 `import video_frame_tool as tool` 后 `tool.process_one` 这类写法仍然可用。

注意：子模块之间用 `from .xxx import name` 复制引用，所以单元测试里
**必须 patch 到调用点所在的子模块**（mock 的常规要求：patch where it is looked up），
例如拦截副本生成要 `patch.object(small_pool, "_build_small_copy")`。

实现细节见项目 README 与各子模块 docstring。
"""

__version__ = '1.0.0'

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

from .platform_compat import (
    APP_NAME,
    IS_LINUX,
    IS_MACOS,
    IS_WINDOWS,
    PLATFORM,
    PLATFORM_LINUX,
    PLATFORM_MACOS,
    PLATFORM_WINDOWS,
    config_file_path,
    detect_platform,
    enable_high_dpi,
    exe_name,
    ffmpeg_search_dirs,
    mono_font_family,
    open_folder,
    user_config_dir,
)

from .constants import (
    CPU_COUNT,
    CRF,
    DEFAULT_HWACCEL,
    DEFAULT_PRESET,
    DEFAULT_SETTINGS,
    DEFAULT_WORKERS,
    HWACCEL_AUTO,
    HWACCEL_MODES,
    HWACCEL_OFF,
    HWACCEL_ON,
    HW_AUTO_STAGES,
    HW_STAGE_COPY,
    HW_STAGE_MAIN,
    HW_STAGE_PIP_RAW,
    HW_STAGE_PIP_SMALL,
    IMAGE_EXTS,
    KEEP_EXT,
    LOG_MAX_LINES,
    MARGIN_BOTTOM,
    MAX_SEGMENTS,
    MONITOR_INTERVAL_MS,
    PIP_BATCH,
    PIP_BATCH_PRESET,
    PIP_BATCH_THREADS,
    PIP_FILL,
    PIP_HEAD_TRIM,
    PIP_H_RATIO,
    PIP_RIGHT_RATIO,
    PIP_SMALL_CRF,
    PIP_SMALL_DIR,
    PIP_SMALL_GOP,
    PIP_SMALL_HEADROOM,
    PIP_SMALL_META,
    PIP_SMALL_MIN_SHORT,
    PIP_SMALL_PRESET,
    PIP_SMALL_ROSTER,
    PIP_SMALL_THREADS,
    PIP_SMALL_V,
    PIP_SPEED,
    PIP_TAIL_TRIM,
    PIP_TOP_RATIO,
    PIP_W_RATIO,
    PRESET_MAP,
    PRODUCT_CHANCE,
    PRODUCT_START_FRAME,
    PRODUCT_START_MAX,
    PRODUCT_WIDTH_RATIO,
    RND_COLOR,
    RND_FLIP_H,
    RND_JITTER,
    RND_ZOOM,
    SCAN_THREADS,
    SEG_MAX,
    SEG_MIN,
    SETTINGS_VERSION,
    VIDEO_EXTS,
)

from .settings import (
    load_settings,
    save_settings,
)
from .settings import (
    _deep_merge,
)

from .sysmon import (
    SystemMonitor,
    fmt_bytes,
    fmt_duration,
)
from .sysmon import (
    _FallbackResReader,
    _LinuxResReader,
    _MacResReader,
    _WindowsResReader,
)

from .logo import (
    LOGO_FILE_NAME,
    LOGO_PNG_B64,
    load_logo,
)
from .logo import (
    _LOGO_EMBEDDED_CACHE,
    _LOGO_PPM_FILES,
    _logo_embedded_png,
    _logo_load_via_ppm,
    _logo_search_paths,
    _logo_shrink,
    _png_decode,
    _png_to_ppm_file,
)

from .ffmpeg_bin import (
    FFMPEG,
    FFPROBE,
)
from .ffmpeg_bin import (
    _find_bin,
)

from .probe import (
    list_images,
    probe_dur_size,
    probe_duration,
    probe_image_size,
    probe_media,
)
from .probe import (
    _IMAGE_PROBE_LOCK,
    _even,
    _list_videos,
    _parse_fps,
    _probe_fallback,
    _probe_image_size_cached,
    _probe_json,
)

from .proc import (
    ProcRegistry,
)
from .proc import (
    _PROGRESS_TIME_RE,
    _check_stopped,
    _read_out_time,
    _run,
)

from .small_pool import (
    prepare_small_pool,
    scan_pip_pool,
    small_cache_stats,
    small_dir_of,
)
from .small_pool import (
    _POOL_CACHE_V,
    _POOL_LOCK,
    _POOL_LOCKS,
    _POOL_MEM,
    _SMALL_WARMING,
    _SMALL_WARM_LOCK,
    _build_small_copy,
    _ensure_small_roster,
    _folder_lock,
    _folder_signature,
    _is_small_copy,
    _pool_cache_path,
    _prepare_small_pool,
    _read_small_meta,
    _run_quiet,
    _small_names,
    _small_plan,
    _small_short_side,
    _stat_pair,
    _warm_small_pool_async,
    _write_small_meta,
)

from .hwaccel import (
    hw_decode_args,
)
from .hwaccel import (
    _small_accel_args,
)

from .pip_track import (
    build_pip_track,
)
from .pip_track import (
    _clip_filter,
    _concat_pip_batches,
    _encode_pip_batch,
    _encode_pip_batches,
    _plan_pip_segments,
)

from .compose import (
    build_command,
)
from .compose import (
    _clamp_prod_start,
    _decide_product,
    _pick_images,
    _safe_probe,
)

from .progress import (
    VideoProgress,
)

from .pipeline import (
    process_one,
)

from .ui.window import (
    App,
)
