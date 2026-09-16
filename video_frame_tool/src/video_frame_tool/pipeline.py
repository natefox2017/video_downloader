"""单条视频流水线：process_one() 串起「低清副本 → 画中画轨 → 主合成」三段。"""

import os
import shutil
import tempfile
import time

from .compose import _clamp_prod_start, _decide_product, _pick_images, build_command
from .constants import KEEP_EXT
from .pip_track import build_pip_track
from .probe import probe_media
from .proc import _check_stopped, _run
from .progress import VideoProgress
from .sysmon import fmt_bytes, fmt_duration

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
