"""短视频批处理工具（GUI）。

功能：把一批主体视频裂变成多份成品，可随机拼接片头/片尾，再逐字复刻参考样本的
容器混淆（本地播放器拒读、平台可播），并保证每份产物的文件哈希互不相同。

按功能拆分成多个子模块；本文件把所有顶层名字重新导出，
因此 `import src as tool` 后 `tool.process_one_fission` 这类写法仍然可用。

实现细节见项目 README 与各子模块 docstring。
"""

__version__ = '2.0.0'

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
    FISSION_DEFAULT_COUNT,
    FISSION_MAX_COUNT,
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

from .sysmon import (
    SystemMonitor,
    fmt_bytes,
    fmt_duration,
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
    _list_videos,
    _parse_fps,
    _probe_fallback,
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

from .obfuscate import (
    process_video as obfuscate_video,
    scan_sources as obfuscate_scan_sources,
)
from .obfuscate import (
    ALGORITHMS,
    DEFAULT_ALGORITHM,
    CLONE_CRF,
    CLONE_FPS,
    CLONE_H,
    CLONE_W,
    CLONE_X264,
    get_algorithm,
)

from .fission import (
    concat_segments,
    output_name,
    pick_segments,
    process_one_fission,
)

from .ui.window import (
    App,
)
