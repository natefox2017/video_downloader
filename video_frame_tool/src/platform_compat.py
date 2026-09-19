"""零、平台适配层。

所有操作系统差异（配置目录、打开目录、等宽字体、ffmpeg 搜索路径、高 DPI）都集中在这里，
其它模块一律不要写 sys.platform 判断。"""

import ntpath
import os
import subprocess
import sys

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

    注意：开启 DPI 感知只是让画面变清晰，**字号跟着 DPI 变大是 Tk 的既定行为**，
    布局要用 ui_scale() 一起放大才不会「字大框小」（下面有详细说明）。
    """
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


# ============================================================================
# 界面布局缩放（Windows 高分屏）
# ============================================================================
# Tk 只有一个 DPI 开关：tk scaling =「一个排版点等于多少屏幕像素」。
#   96 DPI（Windows 100% 缩放）→ 96/72 ≈ 1.333；150% → Windows 报 144 DPI，
#   因为启动时调了 SetProcessDpiAwareness(1)，Tk 会把 tk scaling 设成 2.0。
#
# 关键点：tk scaling **只影响「字号是正数」的字体**（正数按"点"解释，会被乘上
# scaling 换成像素）：10 号字在 100% 下是 13 px、150% 下是 20 px —— 这是对的。
# 但界面里写死的像素（窗口 864×500、padx=20、滑块 length=180…）一个都不会长，
# 于是字号相对窗口显得巨大、控件被挤在一起甚至裁掉。这就是 Windows 高分屏下
# 「字体变得很大」的真正原因：**字体按 DPI 放大了，布局没有**。
#
# 做法：布局按同一个倍数放大，倍数 = 当前 tk scaling ÷ 96 DPI 基准。
TK_SCALING_BASELINE = 96.0 / 72.0      # 96 DPI 下 Tk 的 pixels-per-point
UI_SCALE_MIN = 1.0                     # 只放大不缩小：缩下去字会挤成一团
UI_SCALE_MAX = 3.0                     # 上限按 300% 缩放封顶
UI_SCALE_ENV = "VFT_UI_SCALE"          # 手动指定倍数（任何平台都生效，便于本机预览）


def ui_scale(tk_scaling, platform=None, override=None):
    """
    把 Tk 的 `tk scaling`（pixels-per-point）换算成**界面布局的放大倍数**。

    :param tk_scaling: 运行时读到的值，即 `root.tk.call("tk", "scaling")`
    :param platform:   指定平台（默认当前平台）
    :param override:   显式倍数；留空时读环境变量 VFT_UI_SCALE（便于在
                       macOS 上按 Windows 150% 的观感预览界面，也方便现场救急）
    :return: float，布局/间距/固定长度都要乘这个数

    返回值语义：1.0 = 和 96 DPI 一样；1.5 = 150% 缩放；2.0 = 200% 缩放。
    只放大不缩小（UI_SCALE_MIN），异常输入一律回落到 1.0，绝不抛异常。
    macOS / Linux 恒为 1.0：那边布局本来就是按逻辑点走的，观感正常，不跟着改。

    注意：Windows 的「设置 → 辅助功能 → 文本大小」还会额外放大系统 UI 字体，
    这个不体现在 tk scaling 里；那种情况下用 VFT_UI_SCALE 手动补一点倍数即可。
    """
    platform = platform or PLATFORM
    env = None if override is not None else os.environ.get(UI_SCALE_ENV)
    for raw in (override, env):
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue                       # 写错的值直接忽略，不打断启动
        if value > 0:
            return max(0.5, min(4.0, value))
    if platform != PLATFORM_WINDOWS:
        return 1.0
    try:
        scaling = float(tk_scaling)
    except (TypeError, ValueError):
        return UI_SCALE_MIN
    if scaling <= 0:
        return UI_SCALE_MIN
    return max(UI_SCALE_MIN, min(UI_SCALE_MAX, scaling / TK_SCALING_BASELINE))
