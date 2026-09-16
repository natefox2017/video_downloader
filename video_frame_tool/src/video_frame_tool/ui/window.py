"""八、图形界面。

窗口布局与任务调度。字号一律跟随系统默认 TkDefaultFont（只加粗、不改 pt），
禁止 style.configure(".", font=...) —— 那会盖掉系统字体设置。
改界面后不要用脚本拉起真实 Tk 窗口做验证，交给用户自己开界面看。"""

from concurrent.futures import CancelledError
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from tkinter import filedialog
from tkinter import messagebox
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from ..compose import _safe_probe
from ..constants import CPU_COUNT, CRF, DEFAULT_HWACCEL, DEFAULT_PRESET, DEFAULT_WORKERS, HWACCEL_MODES, LOG_MAX_LINES, MONITOR_INTERVAL_MS, PRESET_MAP, PRODUCT_CHANCE, PRODUCT_START_FRAME, PRODUCT_START_MAX, SETTINGS_VERSION
from ..ffmpeg_bin import FFMPEG
from ..logo import load_logo
from ..pipeline import process_one
from ..platform_compat import IS_MACOS, PLATFORM, mono_font_family, open_folder
from ..probe import _list_videos, list_images, probe_image_size
from ..proc import ProcRegistry, _check_stopped
from ..settings import load_settings, save_settings
from ..small_pool import _small_short_side, _warm_small_pool_async, prepare_small_pool, scan_pip_pool, small_dir_of
from ..sysmon import SystemMonitor, fmt_bytes, fmt_duration

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
        ttk.Label(run_options, text="硬件解码").pack(side="left", padx=(8, 6))
        ttk.Combobox(run_options, width=6, state="readonly", textvariable=self.hwaccel_var,
                     values=HWACCEL_MODES).pack(side="left")

        # 商品图参数单独一行：显示概率与起始帧语义上都挂在「主图目录」这条链上，
        # 放在这里跟运行参数同区，路径区就只剩"选目录"这一件事。
        prod_row = ttk.Frame(f_run)
        prod_row.pack(fill="x", pady=(10, 0))
        # 显示概率：0 = 完全不叠加商品图（此时主图目录可以留空），100 = 每条视频必取一张。
        ttk.Label(prod_row, text="显示概率").pack(side="left")
        ttk.Spinbox(prod_row, from_=0, to=100, width=5,
                    textvariable=self.prod_chance_var).pack(side="left", padx=(6, 2))
        ttk.Label(prod_row, text="%").pack(side="left")
        ttk.Label(prod_row, text="0=不叠加，100=每条必取一张（每条视频独立掷骰）",
                  style="Muted.TLabel").pack(side="left", padx=(6, 20))
        # 产品图起始帧：默认第 60 帧（填 0 表示从第一帧就显示）
        ttk.Label(prod_row, text="主图从第").pack(side="left")
        ttk.Spinbox(prod_row, from_=0, to=PRODUCT_START_MAX, width=6,
                    textvariable=self.prod_start_var).pack(side="left", padx=(4, 2))
        ttk.Label(prod_row, text="帧开始显示").pack(side="left")

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
