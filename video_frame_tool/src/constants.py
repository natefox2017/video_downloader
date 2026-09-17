"""一、默认参数与常量。

界面默认值、编码档位、拼接/混淆参数、素材扩展名都在这里。"""

import os

# ============================================================================
# 一、默认参数与常量
# ============================================================================
# 说明：这里不预设任何素材路径。程序会把用户"上次选择过的路径"记录在
#       settings.json 里（位置见 platform_compat 的 user_config_dir），
#       下次启动自动回填；首次运行需要用户手动选择一次。
# ============================================================================

# ---- 编码参数 ----
CRF = 20

# ---- 片头/片尾拼接 + 混淆裂变（见 fission.py） ----
HD_DEFAULT_COUNT = 1
HD_MAX_COUNT = 10
TL_DEFAULT_COUNT = 1
TL_MAX_COUNT = 10
FISSION_DEFAULT_COUNT = 1
FISSION_MAX_COUNT = 99
# 成品固定分辨率 = 参考样本 22.mp4（720x1276）
OUT_W, OUT_H = 720, 1276
OUT_FPS = 30
OUT_PRESET = "superfast"
OUT_AUDIO_ARGS = ("-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2")

# ---- 其它 ----
CPU_COUNT = os.cpu_count() or 4
LOG_MAX_LINES = 20

# ---- 并发数（界面不持久化，每次启动按 CPU 自动算默认值） ----
# 每个 ffmpeg 的线程配额 = CPU_COUNT // workers，默认取核数一半 → 每个 ffmpeg 约 2 线程。
WORKERS_MAX = 8
WORKERS_DEFAULT = max(1, min(WORKERS_MAX, CPU_COUNT // 2))

# ---- 配置文件结构（默认值的唯一来源：上面的常量） ----
SETTINGS_VERSION = 2
DEFAULT_SETTINGS = {
    "version": SETTINGS_VERSION,
    "paths": {
        "video_dir": "",                # 主体视频目录（待处理/待混淆）
        "head_dir": "",                 # 片头视频目录
        "tail_dir": "",                 # 片尾视频目录
        "out_dir": "",                  # 成品输出目录；留空 = 桌面/out（不存在自动新建）
    },
    "fission": {
        "head_on": False,
        "head_count": HD_DEFAULT_COUNT,
        "tail_on": False,
        "tail_count": TL_DEFAULT_COUNT,
        "ob_on": True,
        "ob_count": FISSION_DEFAULT_COUNT,
    },
}

VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".flv", ".wmv",
    ".webm", ".ts", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".rmvb",
}
