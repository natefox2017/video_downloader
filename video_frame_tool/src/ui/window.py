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

from ..constants import (CPU_COUNT, FISSION_DEFAULT_COUNT, FISSION_MAX_COUNT,
                         HD_DEFAULT_COUNT, HD_MAX_COUNT, LOG_MAX_LINES,
                         TL_DEFAULT_COUNT, TL_MAX_COUNT,
                         SETTINGS_VERSION, WORKERS_DEFAULT, WORKERS_MAX)
from ..ffmpeg_bin import FFMPEG
from ..fission import process_one_fission
from ..obfuscate import ALGORITHMS, DEFAULT_ALGORITHM
from ..logo import load_logo
from ..platform_compat import IS_MACOS, PLATFORM, mono_font_family
from ..probe import _list_videos
from ..proc import ProcRegistry
from ..settings import load_settings, save_settings
from ..sysmon import fmt_duration

# ============================================================================
# 八、图形界面
# ============================================================================


class App(tk.Tk):
    """主窗口：片头/片尾拼接 + 混淆裂变，三个功能各自独立开关、可任意组合。"""

    def __init__(self):
        super().__init__()
        self.title("短视频批处理工具")
        # 固定宽度（原 1080 的 80%，不随屏幕缩放）；高度在 _build_ui 之后按内容
        # 实际需要自适应（见 _fit_height），避免底部留大片空白或内容被裁。
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        except Exception:
            sw, sh = 1440, 900
        width = 864
        height = 560
        self.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(24, (sh - height) // 3)}")
        self.minsize(width, 520)
        self.resizable(False, False)

        # ---- 窗口图标 ----
        self.icon_image = None
        try:
            icon, _src = load_logo(256)
            if icon:
                self.icon_image = icon
                self.iconphoto(True, icon)
        except Exception:
            pass

        # ---- 读取上次配置 ----
        self.settings = load_settings()
        sp = self.settings["paths"]
        sf = self.settings["fission"]

        # ---- 路径 ----
        self.dir_var = tk.StringVar(value=sp.get("video_dir", ""))
        self.head_dir_var = tk.StringVar(value=sp.get("head_dir", ""))
        self.tail_dir_var = tk.StringVar(value=sp.get("tail_dir", ""))
        self.out_dir_var = tk.StringVar(value=sp.get("out_dir", ""))

        # ---- 三个功能开关（独立，可任意组合） ----
        self.head_on = tk.BooleanVar(value=bool(sf.get("head_on", False)))
        self.tail_on = tk.BooleanVar(value=bool(sf.get("tail_on", False)))
        self.ob_on = tk.BooleanVar(value=bool(sf.get("ob_on", True)))
        # 混淆算法（radio 单选，值 = 算法名；默认取注册表里的默认算法）
        _algo = str(sf.get("ob_algorithm", DEFAULT_ALGORITHM))
        if _algo not in ALGORITHMS:
            _algo = DEFAULT_ALGORITHM
        self.ob_algorithm = tk.StringVar(value=_algo)
        # 处理数量上限（slider）：默认 = 混淆视频文件夹里的视频总数，
        # 拖到 N 只处理排序后的前 N 个；换文件夹自动重置为新总数。不持久化。
        self.limit_var = tk.IntVar(value=1)
        self._video_total = 0

        # ---- 数量 ----
        try:
            _hd = int(float(str(sf.get("head_count", HD_DEFAULT_COUNT))))
        except Exception:
            _hd = HD_DEFAULT_COUNT
        self.head_count_var = tk.IntVar(value=max(1, min(HD_MAX_COUNT, _hd)))
        try:
            _tl = int(float(str(sf.get("tail_count", TL_DEFAULT_COUNT))))
        except Exception:
            _tl = TL_DEFAULT_COUNT
        self.tail_count_var = tk.IntVar(value=max(1, min(TL_MAX_COUNT, _tl)))
        try:
            _ob = int(float(str(sf.get("ob_count", FISSION_DEFAULT_COUNT))))
        except Exception:
            _ob = FISSION_DEFAULT_COUNT
        self.ob_count_var = tk.IntVar(value=max(1, min(FISSION_MAX_COUNT, _ob)))
        # 并发数不持久化：每次启动按 CPU 核数自动算默认值，用户本次会话内可改。
        self.workers_var = tk.IntVar(value=WORKERS_DEFAULT)

        # ---- 运行时状态 ----
        self.msg_q = queue.Queue()
        self.registry = ProcRegistry()
        self.stop_event = threading.Event()
        self.running = False
        self._poll_id = None

        self._build_ui()
        self.out_dir_var.trace_add("write", lambda *_: self._refresh_out_label())
        self.dir_var.trace_add("write", lambda *_: self._refresh_limit())
        self._refresh_out_label()
        self._refresh_limit()
        self._fit_height(sh)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_id = self.after(120, self._poll_queue)
        self._check_env()
        self.after_idle(self._present_window)

    def _present_window(self):
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

    def _fit_height(self, sh=None):
        """窗口高度按内容实际需要自适应：内容少不留空白、内容多不被裁。宽度固定 864。"""
        try:
            self.update_idletasks()
            if sh is None:
                sh = self.winfo_screenheight()
            sw = self.winfo_screenwidth()
            width = 864
            req = self.winfo_reqheight()          # 内容区所需高度（不含标题栏）
            height = max(520, min(req + 12, 720, sh - 160))
        except Exception:
            return
        try:
            self.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(24, (sh - height) // 3)}")
        except Exception:
            pass

    def _effective_out_dir(self):
        """输出目录：未选择时默认 ~/Desktop/out。"""
        p = self.out_dir_var.get().strip()
        if p:
            return p
        return os.path.join(os.path.expanduser("~"), "Desktop", "out")

    def _refresh_out_label(self):
        """输出到按钮右边显示当前输出路径。"""
        self.out_path_label.configure(text=self._clip_text(self._effective_out_dir()))

    @staticmethod
    def _clip_text(s, limit=44):
        """按显示宽度截断（中日韩字符按 2 个半角计），保留末尾（文件名在尾部）。"""
        def units(t):
            return sum(2 if ord(c) > 0x2E80 else 1 for c in t)

        if units(s) <= limit:
            return s
        total, cut = 0, len(s)
        for i in range(len(s) - 1, -1, -1):
            total += 2 if ord(s[i]) > 0x2E80 else 1
            if total > limit - 1:
                cut = i + 1
                break
        return "…" + s[cut:]

    # ---------------------------------------------------- 配置持久化
    def _snapshot(self):
        def num(var, default, lo, hi):
            try:
                v = int(float(str(var.get())))
            except Exception:
                v = default
            return max(lo, min(hi, v))
        return {
            "version": SETTINGS_VERSION,
            "paths": {
                "video_dir": self.dir_var.get().strip(),
                "head_dir": self.head_dir_var.get().strip(),
                "tail_dir": self.tail_dir_var.get().strip(),
                "out_dir": self.out_dir_var.get().strip(),
            },
            "fission": {
                "head_on": bool(self.head_on.get()),
                "head_count": num(self.head_count_var, HD_DEFAULT_COUNT, 1, HD_MAX_COUNT),
                "tail_on": bool(self.tail_on.get()),
                "tail_count": num(self.tail_count_var, TL_DEFAULT_COUNT, 1, TL_MAX_COUNT),
                "ob_on": bool(self.ob_on.get()),
                "ob_count": num(self.ob_count_var, FISSION_DEFAULT_COUNT, 1, FISSION_MAX_COUNT),
                "ob_algorithm": self.ob_algorithm.get(),
            },
        }

    def _save_settings(self):
        self.settings = self._snapshot()
        save_settings(self.settings)

    def _on_close(self):
        self._save_settings()
        self.stop_event.set()
        self.registry.kill_all()
        for attr in ("_poll_id",):
            try:
                tid = getattr(self, attr, None)
                if tid:
                    self.after_cancel(tid)
                    setattr(self, attr, None)
            except Exception:
                pass
        self.destroy()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        style = ttk.Style(self)
        if PLATFORM == "macos" and "aqua" in style.theme_names():
            style.theme_use("aqua")
        default_font = tkfont.nametofont("TkDefaultFont")
        family = default_font.actual("family")
        size = default_font.actual("size")
        style.configure("Section.TLabel", font=(family, size, "bold"))
        style.configure("Status.TLabel", font=(family, size, "bold"))
        style.configure("Muted.TLabel", foreground="#647080")
        style.configure("Card.TLabelframe", padding=4)
        style.configure("Card.TLabelframe.Label",
                        font=(family, size, "bold"))

        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=20, pady=12)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)      # 手动拉大窗口时由日志区吃掉多余空间

        def card(row, title):
            box = ttk.Labelframe(root, text=title, style="Card.TLabelframe",
                                 padding=(12, 8, 12, 10))
            box.grid(row=row, column=0, sticky="ew", padx=14, pady=(12, 0))
            box.columnconfigure(1, weight=1)
            return box

        # ---------- 模块一：拼接片头片尾 ----------
        box1 = card(0, "拼接片头片尾")
        # 片头
        ttk.Checkbutton(box1, text="片头", variable=self.head_on,
                        command=self._update_start_btn).grid(row=0, column=0, sticky="w")
        ttk.Entry(box1, textvariable=self.head_dir_var).grid(row=0, column=1, sticky="ew", pady=2)
        hd = ttk.Frame(box1)
        hd.grid(row=0, column=2, padx=(10, 0))
        ttk.Label(hd, text="随机拼").pack(side="left")
        ttk.Spinbox(hd, from_=1, to=HD_MAX_COUNT, width=3,
                    textvariable=self.head_count_var).pack(side="left", padx=(4, 2))
        ttk.Label(hd, text="个").pack(side="left")
        ttk.Button(hd, text="选文件夹…", width=10,
                   command=lambda: self._pick_dir(self.head_dir_var, "选择片头视频文件夹")
                   ).pack(side="left", padx=(8, 0))
        # 片尾
        ttk.Checkbutton(box1, text="片尾", variable=self.tail_on,
                        command=self._update_start_btn).grid(row=1, column=0, sticky="w")
        ttk.Entry(box1, textvariable=self.tail_dir_var).grid(row=1, column=1, sticky="ew", pady=2)
        tl = ttk.Frame(box1)
        tl.grid(row=1, column=2, padx=(10, 0))
        ttk.Label(tl, text="随机拼").pack(side="left")
        ttk.Spinbox(tl, from_=1, to=TL_MAX_COUNT, width=3,
                    textvariable=self.tail_count_var).pack(side="left", padx=(4, 2))
        ttk.Label(tl, text="个").pack(side="left")
        ttk.Button(tl, text="选文件夹…", width=10,
                   command=lambda: self._pick_dir(self.tail_dir_var, "选择片尾视频文件夹")
                   ).pack(side="left", padx=(8, 0))

        # ---------- 模块二：混淆视频 ----------
        box2 = card(1, "混淆视频")
        # 勾选 = 对这些视频做混淆
        ttk.Checkbutton(box2, text="视频", variable=self.ob_on,
                        command=self._update_start_btn).grid(row=0, column=0, sticky="w")
        # 视频路径 + 混淆份数（同一行）+ 选文件夹
        ttk.Entry(box2, textvariable=self.dir_var).grid(row=0, column=1, sticky="ew", pady=2)
        ob = ttk.Frame(box2)
        ob.grid(row=0, column=2, padx=(10, 0))
        ttk.Label(ob, text="混淆成").pack(side="left")
        ttk.Spinbox(ob, from_=1, to=FISSION_MAX_COUNT, width=4,
                    textvariable=self.ob_count_var).pack(side="left", padx=(4, 2))
        ttk.Label(ob, text="份").pack(side="left")
        ttk.Button(box2, text="选文件夹…", width=10,
                   command=lambda: self._pick_dir(self.dir_var, "选择视频文件夹")
                   ).grid(row=0, column=3, padx=(10, 0))
        # 处理数量 slider + 混淆算法（radio 单选，按日期命名）同一行
        algo_row = ttk.Frame(box2)
        algo_row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(algo_row, text="只混淆前").pack(side="left")
        self.limit_scale = ttk.Scale(algo_row, from_=1, to=1, length=150,
                                     variable=self.limit_var,
                                     command=lambda *_: self._update_limit_label())
        self.limit_scale.pack(side="left", padx=(4, 4))
        self.limit_label = ttk.Label(algo_row, text="—", width=16, anchor="w")
        self.limit_label.pack(side="left")
        ttk.Label(algo_row, text="混淆算法：").pack(side="left", padx=(12, 0))
        for name, algo in ALGORITHMS.items():
            ttk.Radiobutton(algo_row, text=algo["label"], value=name,
                            variable=self.ob_algorithm).pack(side="left", padx=(4, 0))

        # ---------- 同时处理 + 输出到（靠左）；开始处理（最右） ----------
        bar = ttk.Frame(root)
        bar.grid(row=2, column=0, sticky="ew", padx=14, pady=(12, 0))
        ttk.Label(bar, text="同时处理").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=WORKERS_MAX, width=4,
                    textvariable=self.workers_var).pack(side="left", padx=(4, 0))
        # 输出目录：只有按钮 + 路径文字（无输入框）；默认 ~/Desktop/out，不存在自动新建
        ttk.Button(bar, text="输出到", width=8,
                   command=lambda: self._pick_dir(self.out_dir_var, "选择输出文件夹")
                   ).pack(side="left", padx=(16, 0))
        self.out_path_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.out_path_label.pack(side="left", padx=(8, 0))
        self.btn_start = ttk.Button(bar, text="开始处理", command=self._toggle_run,
                                    default="active", width=12)
        self.btn_start.pack(side="right")

        # ---------- 进度 ----------
        f_prog = ttk.Frame(root)
        f_prog.grid(row=3, column=0, sticky="ew", padx=14, pady=(10, 0))
        f_prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(f_prog, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_text = ttk.Label(f_prog, text="就绪", width=12, anchor="e",
                                       style="Status.TLabel")
        self.progress_text.grid(row=0, column=1, sticky="e", padx=(12, 0))

        # ---------- 日志（初始按内容定高；窗口被拉大时随之长高） ----------
        f_log = ttk.Labelframe(root, text="日志", style="Card.TLabelframe",
                               padding=(12, 8, 12, 10))
        f_log.grid(row=4, column=0, sticky="nsew", padx=14, pady=(10, 14))
        f_log.columnconfigure(0, weight=1)
        f_log.rowconfigure(0, weight=1)
        self.log_box = tk.Text(f_log, height=6, wrap="word",
                               font=(mono_font_family(), 11), relief="flat", borderwidth=0,
                               padx=8, pady=8)
        self.log_box.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f_log, orient="vertical", command=self.log_box.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_box.configure(yscrollcommand=sb.set, state="disabled")

        self._update_start_btn()

    # ------------------------------------------------------ 选择/开关
    def _pick_dir(self, var, title):
        p = filedialog.askdirectory(title=title)
        if p:
            var.set(p)
            self._save_settings()

    def _refresh_limit(self, *_):
        """混淆视频文件夹变化时：重算视频总数，slider 量程随之更新并重置为总数。"""
        d = self.dir_var.get().strip()
        try:
            n = len(_list_videos(d)) if d and os.path.isdir(d) else 0
        except Exception:
            n = 0
        self._video_total = n
        if n > 0:
            self.limit_scale.configure(state="normal", from_=1, to=n)
            self.limit_var.set(n)          # 默认 = 文件夹里视频总数
        else:
            self.limit_scale.configure(state="disabled", from_=1, to=1)
            self.limit_var.set(1)
        self._update_limit_label()

    def _update_limit_label(self, *_):
        """slider 旁的文案：「只混淆前 N 个（共 M 个）」；无视频时显示 —。"""
        total = self._video_total
        try:
            v = int(float(self.limit_var.get()))
        except Exception:
            v = 1
        self.limit_label.configure(text=("%d 个（共 %d 个）" % (min(v, total), total))
                                   if total else "—")

    def _update_start_btn(self):
        """三开关全关时禁用开始按钮；否则启用。"""
        any_on = self.head_on.get() or self.tail_on.get() or self.ob_on.get()
        if self.running:
            return
        self.btn_start.configure(state="normal" if any_on else "disabled")

    def _check_env(self):
        if not FFMPEG:
            self._log("✗ 没有找到 ffmpeg，暂时无法处理视频。请先安装：")
            self._log("     Windows：winget install Gyan.FFmpeg   或  scoop install ffmpeg")
            self._log("     macOS  ：brew install ffmpeg")
            self._log("     Linux  ：sudo apt install ffmpeg")
            self._log("     通用兜底：pip install imageio-ffmpeg")
            self.btn_start.configure(state="disabled")
            return
        stale = [label for label, path in (
            ("主体视频目录", self.dir_var.get()),
            ("片头目录", self.head_dir_var.get()),
            ("片尾目录", self.tail_dir_var.get()),
            ("输出目录", self.out_dir_var.get()))
            if path and not os.path.isdir(path)]
        if stale:
            self._log("路径不存在，请重新选择：" + "、".join(stale))
        # 默认输出目录（桌面/out）不存在则新建
        out_dir = self._effective_out_dir()
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                self._log(f"✗ 无法创建输出目录 {out_dir}：{e}")

    # ------------------------------------------------------ 主流程
    def _toggle_run(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if self.running:
            return
        if not FFMPEG:
            messagebox.showerror("错误", "没有找到 ffmpeg，无法处理视频")
            return

        head_on = self.head_on.get()
        tail_on = self.tail_on.get()
        ob_on = self.ob_on.get()
        if not (head_on or tail_on or ob_on):
            messagebox.showinfo("提示", "请至少开启片头、片尾或混淆中的一个功能")
            return

        vdir = self.dir_var.get().strip()
        if not vdir or not os.path.isdir(vdir):
            messagebox.showwarning("路径无效", f"主体视频文件夹不存在：{vdir or '（未选择）'}")
            return

        files = _list_videos(vdir)
        if not files:
            messagebox.showinfo("提示", "主体视频文件夹里没有找到视频文件")
            return

        # slider 限制：只处理排序后的前 N 个（默认 N = 全部）
        try:
            limit = max(1, int(float(self.limit_var.get())))
        except Exception:
            limit = len(files)
        if limit < len(files):
            files = files[:limit]

        head_pool = tail_pool = []
        if head_on:
            hd = self.head_dir_var.get().strip()
            if not hd or not os.path.isdir(hd):
                messagebox.showwarning("路径无效", f"片头文件夹不存在：{hd or '（未选择）'}")
                return
            head_pool = _list_videos(hd)
            if not head_pool:
                messagebox.showinfo("提示", "片头文件夹里没有找到视频文件")
                return
        if tail_on:
            tl = self.tail_dir_var.get().strip()
            if not tl or not os.path.isdir(tl):
                messagebox.showwarning("路径无效", f"片尾文件夹不存在：{tl or '（未选择）'}")
                return
            tail_pool = _list_videos(tl)
            if not tail_pool:
                messagebox.showinfo("提示", "片尾文件夹里没有找到视频文件")
                return

        try:
            head_count = int(float(str(self.head_count_var.get())))
        except Exception:
            head_count = HD_DEFAULT_COUNT
        head_count = max(1, min(HD_MAX_COUNT, head_count)) if head_on else 0
        try:
            tail_count = int(float(str(self.tail_count_var.get())))
        except Exception:
            tail_count = TL_DEFAULT_COUNT
        tail_count = max(1, min(TL_MAX_COUNT, tail_count)) if tail_on else 0
        try:
            ob_count = int(float(str(self.ob_count_var.get())))
        except Exception:
            ob_count = FISSION_DEFAULT_COUNT
        ob_count = max(1, min(FISSION_MAX_COUNT, ob_count)) if ob_on else 1
        try:
            workers = int(float(str(self.workers_var.get())))
        except Exception:
            workers = WORKERS_DEFAULT
        workers = max(1, min(WORKERS_MAX, workers))

        # 输出目录 = 「输出到」选择的目录；未选则默认桌面/out（启动时已自动新建）
        out_dir = self._effective_out_dir()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("无法创建输出目录", str(e))
            return

        self.running = True
        self.stop_event.clear()
        self._save_settings()
        self.btn_start.configure(text="停止")
        self.progress.configure(mode="determinate", value=0, maximum=100)
        self._set_progress_text("准备中…")
        self._clear_log()

        _total_all = self._video_total
        self._log(f"开始处理：{len(files)} 个主体视频"
                  + (f"（文件夹共 {_total_all} 个，按 slider 只处理前 {len(files)} 个）"
                     if 0 < len(files) < _total_all else "")
                  + f"，每个裂变 {ob_count} 份，共 {len(files) * ob_count} 份，"
                    f"同时处理 {workers} 个")
        self._log(f"片头：{'开（随机拼 ' + str(head_count) + ' 个）' if head_on else '关'}")
        self._log(f"片尾：{'开（随机拼 ' + str(tail_count) + ' 个）' if tail_on else '关'}")
        self._log(f"混淆：{'开（本地不可播放，逐字复刻样本）' if ob_on else '关（输出标准 MP4）'}")
        if ob_on:
            self._log(f"混淆算法：{self.ob_algorithm.get()}")
        self._log(f"输出目录：{out_dir}")
        self._log(f"分辨率固定：720x1276（对齐参考样本）")

        threading.Thread(
            target=self._worker,
            args=(files, head_pool, tail_pool, out_dir, head_count, tail_count,
                  ob_on, ob_count, workers, self.ob_algorithm.get()),
            daemon=True).start()

    def _worker(self, files, head_pool, tail_pool, out_dir, head_count, tail_count,
                ob_on, ob_count, workers, algorithm):
        """后台线程：并发处理。进度按真实个数（完成份数 / 总份数）汇报。"""
        total = len(files) * ob_count
        ok = fail = 0
        t0 = time.time()
        self.msg_q.put(("count", (0, total)))
        threads = max(1, CPU_COUNT // max(1, min(workers, len(files))))

        def run_one(path):
            nonlocal_ok = None
            return process_one_fission(
                path, head_pool, tail_pool, out_dir, FFMPEG, self.registry,
                self.stop_event, head_count=head_count, tail_count=tail_count,
                ob_on=ob_on, ob_count=ob_count, threads=threads,
                log=self._log_q, tag=f"[{os.path.basename(path)}]",
                algorithm=algorithm)

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(files)))) as ex:
            futures = {ex.submit(run_one, f): f for f in files}
            for fut in as_completed(futures):
                name = os.path.basename(futures[fut])
                try:
                    outs = fut.result()
                    ok += len(outs)
                except CancelledError:
                    pass
                except Exception as e:
                    if not self.stop_event.is_set():
                        fail += ob_count
                        self._log_q(f"[{name}] ✗ 失败：{e}")
                self.msg_q.put(("count", (ok + fail, total)))
        self.msg_q.put(("done", (ok, fail, time.time() - t0)))

    def _stop(self):
        if not self.running:
            return
        self.stop_event.set()
        self.registry.kill_all()
        self.btn_start.configure(text="正在停止…", state="disabled")
        self._set_progress_text("正在停止…")

    def _finish(self, payload):
        ok, fail, cost = payload
        self.running = False
        self.btn_start.configure(text="开始处理")
        self._update_start_btn()
        stopped = self.stop_event.is_set()
        self._draw_progress(100.0 if not stopped else self._percent(ok + fail, ok + fail))
        state = "已停止" if stopped else "全部完成"
        self._set_progress_text(state)
        done = ok + fail
        self._log(f"—— {state}：成功 {ok} 份，失败 {fail} 份，"
                  f"总用时 {fmt_duration(cost)}"
                  + (f"（平均每份 {fmt_duration(cost / done)}）" if done else "") + " ——")

    # ------------------------------------------------------ 日志/进度
    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self._trim_log()
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_batch(self, lines):
        if not lines:
            return
        self.log_box.configure(state="normal")
        self.log_box.insert("end", "\n".join(lines) + "\n")
        self._trim_log()
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_q(self, text):
        self.msg_q.put(("log", text))

    def _trim_log(self):
        try:
            total = int(self.log_box.index("end-1c").split(".")[0])
            if total > LOG_MAX_LINES:
                self.log_box.delete("1.0", f"{total - LOG_MAX_LINES}.0")
        except Exception:
            pass

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    @staticmethod
    def _percent(done, total):
        return 100.0 * float(done) / float(total) if total else 0.0

    def _draw_progress(self, percent):
        pct = max(0.0, min(100.0, float(percent)))
        self.progress.configure(mode="determinate", maximum=100, value=pct)
        self.progress_text.configure(text=f"{pct:.0f}%")

    def _set_progress_text(self, text):
        self.progress_text.configure(text=text)

    # ------------------------------------------------------ 消息泵
    def _poll_queue(self):
        logs, count, done = [], None, None
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    logs.append(payload)
                elif kind == "count":
                    count = payload
                elif kind == "done":
                    done = payload
        except queue.Empty:
            pass
        if count is not None:
            d, t = count
            self.progress.configure(mode="determinate", maximum=100,
                                    value=self._percent(d, t))
            self.progress_text.configure(text=f"{d}/{t}")
        self._log_batch(logs)
        if done:
            self._finish(done)
        self._poll_id = self.after(120, self._poll_queue)

    # ------------------------------------------------------ 系统监控
    # （已按用户要求移除 CPU/内存监控显示；sysmon.py 的采样工具保留备用）
