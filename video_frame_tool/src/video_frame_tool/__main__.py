"""命令行入口：python -m video_frame_tool"""

import sys

from .ffmpeg_bin import FFMPEG
from .platform_compat import enable_high_dpi
from .ui.window import App


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
