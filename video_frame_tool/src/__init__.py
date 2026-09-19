"""短视频批处理工具（GUI）。

功能：把搬运视频目录里的视频逐条加工成成品，每条可随机拼接前贴/尾贴、可用封面图
替换第 0 帧，再走「复刻 22.mp4」的上下场混合 + 容器混淆（本地播放器拒读、平台可播），
并保证每条产物的文件哈希互不相同。

按功能拆分成多个子模块；本文件把所有顶层名字重新导出，
因此 `import src as tool` 后 `tool.process_one_output` 这类写法仍然可用。

实现细节见项目 README 与各子模块 docstring。
"""

__version__ = '3.1.0'

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
    DEFAULT_SETTINGS,
    HD_DEFAULT_COUNT,
    HD_MAX_COUNT,
    LOG_MAX_LINES,
    OUT_AUDIO_ARGS,
    OUT_FPS,
    OUT_H,
    OUT_PRESET,
    OUT_W,
    SETTINGS_VERSION,
    TL_DEFAULT_COUNT,
    TL_MAX_COUNT,
    VIDEO_EXTS,
    WORKERS_DEFAULT,
    WORKERS_MAX,
)

from .settings import (
    load_settings,
    save_settings,
)
from .settings import (
    _deep_merge,
)

from .logo import (
    load_logo,
)

from .ffmpeg_bin import (
    FFMPEG,
    FFPROBE,
)

from .probe import (
    probe_media,
)
from .probe import (
    probe_frames,
)
from .probe import (
    _list_videos,
    _parse_fps,
    _probe_fallback,
    _probe_json,
    list_images,
)

from .proc import (
    ProcRegistry,
    fmt_duration,
)
from .proc import (
    _PROGRESS_TIME_RE,
    _check_stopped,
    _read_out_time,
    _run,
)

from .obfuscate import (
    process_video as obfuscate_video,
)
from .obfuscate import (
    ALGORITHMS,
    DEFAULT_ALGORITHM,
    CLONE_CRF,
    CLONE_FPS,
    CLONE_H,
    CLONE_W,
    CLONE_X264,
    FIELD_DURATION_MS,
    get_algorithm,
)

from .fission import (
    concat_segments,
    output_name,
    pick_segments,
    process_batch,
    process_one_output,
)

from .ui.window import (
    App,
)
