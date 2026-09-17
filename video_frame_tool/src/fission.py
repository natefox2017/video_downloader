"""「片头 + 主体 + 片尾」拼接 + 混淆裂变（界面主功能的唯一核心逻辑）。

目标：把一条主体视频裂变成 M 份成品，每份可带随机片头/片尾，再逐字复刻样本 22.mp4
的容器混淆，让本地播放器拒读、平台（快手）可播，且每份产物的文件哈希必不同。

流程（三个开关各自独立，可任意组合）：
  1. 片头（可选）：从片头目录随机抽 N 个视频，拼在主体前面；
  2. 片尾（可选）：从片尾目录随机抽 N 个视频，拼在主体后面；
  3. 混淆（可选）：把拼接产物走 obfuscate.process_video 的容器改写（本地拒收）；
    不开混淆时输出标准 MP4（本地可播）。

分辨率固定 720x1276（对齐样本 22.mp4），不随片头/主体/片尾变化。
片头/片尾每份**独立随机抽取**；混淆每份独立随机（见 obfuscate._fake_meta），保证哈希必不同。

命名：主体视频名 + `_` + 两位序号（01~99），便于按来源区分、按序号排序。
"""

import os
import random
import shutil
import tempfile
from concurrent.futures import CancelledError

from .constants import (OUT_AUDIO_ARGS, OUT_FPS, OUT_H, OUT_PRESET, OUT_W, CRF)
from .proc import _run

# ============================================================================
# 一、参数
# ============================================================================


def pick_segments(pool, count, rng=random):
    """从片头/片尾池里随机挑 count 个（够挑无放回，不够有放回凑满）。返回新列表。"""
    count = max(0, int(count))
    if count <= 0 or not pool:
        return []
    if len(pool) >= count:
        return rng.sample(pool, count)
    return rng.choices(pool, k=count)


def output_name(stem, seq):
    """成品名 = 主体视频名 + `_` + 两位序号（01~99）。"""
    return "%s_%02d.mp4" % (stem, int(seq))


# ============================================================================
# 二、段编码与拼接
# ============================================================================


def _seg_command(ffmpeg, src, dst, has_audio, threads):
    """把一段视频重编码成固定规格（720x1276 / 30fps），各段参数一致才能无损拼接。"""
    vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
          f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"fps={OUT_FPS},format=yuv420p")
    cmd = [ffmpeg, "-y", "-nostats", "-progress", "pipe:1", "-i", src]
    if has_audio:
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-vf", vf,
            "-c:v", "libx264", "-preset", OUT_PRESET, "-crf", str(CRF),
            "-pix_fmt", "yuv420p", *OUT_AUDIO_ARGS,
            "-threads", str(threads), dst]
    return cmd


def concat_segments(segments, out_path, ffmpeg, registry, stop_event,
                    threads=2, log=None, tag="", progress=None):
    """把「片头 + 主体 + 片尾」按顺序拼成一条标准 MP4，返回成品路径。

    segments: [(路径, has_audio, 时长秒)]，顺序 = 最终成片顺序。
    各段先各自重编码成相同规格，再 concat demuxer `-c copy` 无损拼接。
    """
    def _stopped():
        if stop_event is not None and stop_event.is_set():
            raise CancelledError("处理已停止")

    def _say(text):
        if log:
            log(f"{tag} {text}" if tag else text)

    total_dur = sum(float(d) for _, _, d in segments) or 0.0

    tmp_dir = tempfile.mkdtemp(prefix=".fs_", dir=os.path.dirname(out_path))
    try:
        done_dur = 0.0
        seg_paths = []
        for idx, (src, has_audio, seg_dur) in enumerate(segments):
            _stopped()
            seg_path = os.path.join(tmp_dir, f"seg_{idx:03d}.mp4")
            cmd = _seg_command(ffmpeg, src, seg_path, has_audio, threads)

            def on_frac(frac, _d=seg_dur):
                if progress and total_dur > 0:
                    progress(min(1.0, (done_dur + frac * _d) / total_dur))

            _run(cmd, registry, on_progress=on_frac if (progress and seg_dur > 0) else None,
                 expected_dur=seg_dur if seg_dur > 0 else 0.0)
            done_dur += seg_dur
            seg_paths.append(seg_path)

        _stopped()
        lst = os.path.join(tmp_dir, "list.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            for p in seg_paths:
                fh.write("file '%s'\n" % p.replace("'", "'\\''"))
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst,
               "-c", "copy", "-movflags", "+faststart", out_path]
        _run(cmd, registry)
        if progress:
            progress(1.0)
        return out_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================================
# 三、单条视频的裂变（拼接 + 混淆，给界面线程调用）
# ============================================================================

def process_one_fission(main, head_pool, tail_pool, out_dir, ffmpeg, registry,
                        stop_event, head_count=0, tail_count=0, ob_on=True,
                        ob_count=1, threads=2, log=None, tag="", progress=None,
                        algorithm=None):
    """把一条主体视频裂变成 ob_count 份成品，返回输出路径列表。

    head_pool/tail_pool: 片头/片尾视频列表（空 = 不拼）。
    head_count/tail_count: 片头/片尾每份随机抽取的个数（0 = 该部分不生效）。
    ob_on: 是否走容器混淆（False 输出标准 MP4，本地可播）。
    ob_count: 每份裂变数量（1~99）。
    progress: 0~1 的整体真实完成度回调（拼接 + 混淆折算）。
    algorithm: 混淆算法名（见 obfuscate.ALGORITHMS）；None 用默认 clone22。
    """
    from .obfuscate import process_video
    from .probe import probe_media

    def _stopped():
        if stop_event is not None and stop_event.is_set():
            raise CancelledError("处理已停止")

    def _say(text):
        if log:
            log(f"{tag} {text}" if tag else text)

    stem = os.path.splitext(os.path.basename(main))[0]
    main_info = probe_media(main)
    results = []

    for k in range(1, int(ob_count) + 1):
        _stopped()
        name = output_name(stem, k)
        dst = os.path.join(out_dir, name)
        if os.path.abspath(dst) == os.path.abspath(main):
            dst = os.path.join(out_dir, stem + f"_已处理_{k:02d}.mp4")

        # ---- 每份独立随机抽取片头/片尾 ----
        heads = pick_segments(head_pool, head_count) if head_count else []
        tails = pick_segments(tail_pool, tail_count) if tail_count else []

        # ---- 组段：片头 + 主体 + 片尾，每段探出 has_audio / 时长 ----
        segments = []
        for p in heads + [main] + tails:
            try:
                info = probe_media(p)
                segments.append((p, info["has_audio"], float(info.get("duration") or 0.0)))
            except Exception as e:
                raise RuntimeError(f"读取视频失败 {os.path.basename(p)}：{e}")

        _say(f"第 {k}/{ob_count} 份：片头 {len(heads)} + 主体 + 片尾 {len(tails)}")

        if heads or tails:
            # 有片头/片尾 → 先拼成标准 MP4 中间件，再走混淆（或不走）
            mid_dir = tempfile.mkdtemp(prefix=".fs_mid_", dir=out_dir)
            try:
                _stopped()
                mid = os.path.join(mid_dir, "joined.mp4")
                concat_segments(segments, mid, ffmpeg, registry, stop_event,
                                threads=threads, log=log, tag=tag)
                if ob_on:
                    _say("拼接完成，走容器混淆…")
                    process_video(mid, out_dir, ffmpeg, registry, stop_event,
                                  log=log, tag=tag, out_name=name,
                                  algorithm=algorithm)
                else:
                    shutil.move(mid, dst)
            finally:
                shutil.rmtree(mid_dir, ignore_errors=True)
        else:
            # 无片头片尾：主体直接走混淆（或直接复制为标准 MP4）
            if ob_on:
                process_video(main, out_dir, ffmpeg, registry, stop_event,
                              log=log, tag=tag, out_name=name,
                              algorithm=algorithm)
            else:
                _say("不混淆，直接输出标准 MP4…")
                _seg_tmp = tempfile.mkdtemp(prefix=".fs_std_", dir=out_dir)
                try:
                    _stopped()
                    _seg = os.path.join(_seg_tmp, "std.mp4")
                    _run(_seg_command(ffmpeg, main, _seg, main_info["has_audio"], threads),
                         registry)
                    shutil.move(_seg, dst)
                finally:
                    shutil.rmtree(_seg_tmp, ignore_errors=True)

        if progress:
            progress((k) / int(ob_count))
        results.append(dst)

    return results
