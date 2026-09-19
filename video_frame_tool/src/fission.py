"""「前贴 + 搬运 + 尾贴」拼接 + 封面 + 复刻22 混淆（界面主功能的唯一核心逻辑）。

一条成品的结构（用户 2026-09-19 定的配方）：

    首帧图 + 前贴×N + 搬运 + 尾贴×N

    1. 封面（可选）：从封面目录随机取 1 张图片，**强制拉伸**到成品尺寸后替换拼接片的
       第 0 帧（时长/帧数不变，只是画面被换掉）；
    2. 前贴（可选）：从前贴目录随机抽 N 个视频，拼在搬运前面；
    3. 搬运：搬运视频目录里的一条（由调用方随机抽取，界面 slider 决定抽几条）；
    4. 尾贴（可选）：从尾贴目录随机抽 N 个视频，拼在搬运后面。

拼好的视频是一条约 720x1276 / 30fps 的标准 MP4，再走 obfuscate 的 fieldmix
（复刻 22.mp4 的上下场混合 + 容器伪装）输出成品：本地播放器拒读、平台可播。

前贴/尾贴/封面**每条成品独立随机抽取**，加上编码时刻参与容器 DateUTC，
保证每条产物的文件哈希必不同。搬运由界面**随机抽取** N 个（N 由 slider 决定），
一个搬运出一条成品；抽完按目录原顺序执行，序号/命名因此稳定可预期。

命名：两位序号（01~99）开头 + `_` + 搬运视频名（如 `01_x31.mp4`），
按文件名排序即按处理顺序排，便于一次性按顺序上传。
"""

import os
import random
import shutil
import tempfile
from concurrent.futures import CancelledError

from .constants import CRF, OUT_AUDIO_ARGS, OUT_FPS, OUT_H, OUT_PRESET, OUT_W
from .proc import _run

# ============================================================================
# 一、参数
# ============================================================================

# 一条成品的耗时有两块：拼接（前贴/搬运/尾贴各段重编码 + 无损合并）与
# 复刻22 混淆（整条再重编码 + 改容器）。两块的重编码像素量都等于影片长度，
# 所以耗时大致相当，按这个比例把两块进度合成「本条完成度」上报给界面。
# 没有前贴/尾贴/封面（只有一条搬运）时跳过拼接，整条进度都由混淆贡献。
CONCAT_WEIGHT = 0.45


def pick_segments(pool, count, rng=random):
    """从前贴/尾贴池里随机挑 count 个（够挑无放回，不够有放回凑满）。返回新列表。"""
    count = max(0, int(count))
    if count <= 0 or not pool:
        return []
    if len(pool) >= count:
        return rng.sample(pool, count)
    return rng.choices(pool, k=count)


def pick_cover(pool, rng=random):
    """从封面图池里随机挑 1 张（空池返回 None）。"""
    return rng.choice(pool) if pool else None


def output_name(stem, seq):
    """成品名 = 搬运视频名 + `_` + 两位序号（01~99）。"""
    return "%02d_%s.mp4" % (int(seq), stem)


# ============================================================================
# 二、段编码与拼接
# ============================================================================


def _seg_command(ffmpeg, src, dst, has_audio, threads, cover=None):
    """把一段视频重编码成固定规格（720x1276 / 30fps），各段参数一致才能无损拼接。

    cover: 封面图路径。非 None 时把图片强制拉伸到成品尺寸（铺满、不留黑边），
    替换本段的第 0 帧（若本段是拼接里的第一段，即成片第 0 帧）。
    """
    norm = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
            f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={OUT_FPS}")
    cmd = [ffmpeg, "-y", "-nostats", "-progress", "pipe:1", "-i", src]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    if cover:
        cmd += ["-i", cover]
    if cover:
        # 输入编号：0=src；无音轨时 1=anullsrc，否则 1=cover
        cover_idx = 2 if not has_audio else 1
        fc = (f"[0:v]{norm}[base];"
              f"[{cover_idx}:v]scale={OUT_W}:{OUT_H},setsar=1,format=yuv420p[cv];"
              f"[base][cv]overlay=enable='eq(n,0)',format=yuv420p[vout]")
        cmd += ["-filter_complex", fc, "-map", "[vout]"]
    else:
        cmd += ["-vf", norm + ",format=yuv420p", "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0"]
    else:
        cmd += ["-map", "1:a:0", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", OUT_PRESET, "-crf", str(CRF),
            "-pix_fmt", "yuv420p", *OUT_AUDIO_ARGS,
            "-threads", str(threads), dst]
    return cmd


def concat_segments(segments, out_path, ffmpeg, registry, stop_event,
                    threads=2, log=None, tag="", progress=None, cover=None):
    """把「前贴 + 搬运 + 尾贴」按顺序拼成一条标准 MP4，返回成品路径。

    segments: [(路径, has_audio, 时长秒)]，顺序 = 最终成片顺序。
    cover: 封面图路径。非 None 时叠到第一个段的第 0 帧上（即成片第 0 帧），
        图片强制拉伸铺满；时长/帧数不变。
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
            seg_cover = cover if idx == 0 else None
            cmd = _seg_command(ffmpeg, src, seg_path, has_audio, threads,
                               cover=seg_cover)

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
# 三、单条成品的生成（给界面线程调用）
# ============================================================================

def process_one_output(main, seq, out_dir, ffmpeg, registry, stop_event,
                       head_pool=None, tail_pool=None, cover_pool=None,
                       head_count=0, tail_count=0, cover_on=False, threads=2,
                       log=None, tag="", algorithm=None, on_progress=None):
    """生成 1 条成品：封面 + 随机前贴 + 搬运 + 随机尾贴 → 拼接 → 复刻22 混淆。

    main:        这条成品用的搬运视频。调用方决定取哪一条（界面随机抽搬运目录里的 N 个）。
    seq:         成品序号（1 起），决定输出文件名 `NN_<搬运名>.mp4`（NN 在开头，如 01_x31.mp4）。
    head_pool/tail_pool: 前贴/尾贴候选池（空 = 不拼）。
    head_count/tail_count: 本条随机抽几个前贴/尾贴（0 = 该部分不生效）。
    cover_pool:  封面图候选池；cover_on 为真时随机取 1 张替换拼接片第 0 帧。
    algorithm:   混淆算法名（见 obfuscate.ALGORITHMS）；None 用默认（复刻22）。
    on_progress: 本条成品的真实完成度回调（0~1）。拼接与混淆两块按 CONCAT_WEIGHT
                 加权合成，数据全部来自 ffmpeg 汇报的已编码秒数（不是估算）。

    返回成品路径。失败时抛异常（由调用方记日志），不吞错误。
    """
    from .obfuscate import process_video
    from .probe import probe_media

    def _stopped():
        if stop_event is not None and stop_event.is_set():
            raise CancelledError("处理已停止")

    def _say(text):
        # 日志只报「第几条」（用户 2026-09-19：别刷屏，来源/组成不必重复报）
        if log:
            log(text)

    stem = os.path.splitext(os.path.basename(main))[0]
    name = output_name(stem, seq)

    # ---- 每条成品独立随机抽取前贴/尾贴/封面 ----
    heads = pick_segments(head_pool, head_count) if head_count else []
    tails = pick_segments(tail_pool, tail_count) if tail_count else []
    cover = pick_cover(cover_pool) if cover_on else None

    # ---- 组段：前贴 + 搬运 + 尾贴，每段探出 has_audio / 时长 ----
    segments = []
    for p in heads + [main] + tails:
        try:
            info = probe_media(p)
        except Exception as e:
            raise RuntimeError(f"读取视频失败 {os.path.basename(p)}：{e}")
        segments.append((p, info["has_audio"], float(info.get("duration") or 0.0)))

    total_dur = sum(d for _, _, d in segments)

    # ---- 进度：拼接占前 CONCAT_WEIGHT 段，混淆占剩下的；无拼接时全算混淆 ----
    concat_w = CONCAT_WEIGHT if (cover or heads or tails) else 0.0

    def _report_concat(frac):
        if on_progress:
            on_progress(min(1.0, max(0.0, frac) * concat_w))

    def _report_ob(frac):
        if on_progress:
            on_progress(min(1.0, concat_w + max(0.0, frac) * (1.0 - concat_w)))

    work_dir = tempfile.mkdtemp(prefix=".fs_mid_", dir=out_dir)
    _say(f"第 {seq} 条：开始处理")
    try:
        _stopped()
        if cover or heads or tails:
            # 有封面/前贴/尾贴 → 拼成标准 MP4 中间件（已统一成 720x1276/30fps）
            mid = os.path.join(work_dir, "joined.mp4")
            concat_segments(segments, mid, ffmpeg, registry, stop_event,
                            threads=threads, log=log, tag=tag, cover=cover,
                            progress=_report_concat if on_progress else None)
        else:
            # 只有搬运：fieldmix 自己会把画面缩放到成品规格，不必先拼一遍
            mid = main

        _stopped()
        process_video(mid, out_dir, ffmpeg, registry, stop_event,
                      duration=total_dur,
                      on_progress=_report_ob if on_progress else None,
                      log=log, tag=tag, out_name=name, algorithm=algorithm)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    _say(f"第 {seq} 条：完成")
    return os.path.join(out_dir, name)


def process_batch(main_files, out_dir, ffmpeg, registry, stop_event,
                  head_pool=None, tail_pool=None, cover_pool=None,
                  head_count=0, tail_count=0, cover_on=False, threads=2,
                  log=None, algorithm=None):
    """按顺序给 main_files 里每条搬运出一条成品，返回成品路径列表。

    界面走并发路径（自己提交任务，见 ui/window.py），这个串行版主要给脚本与测试用。
    """
    outs = []
    for i, main in enumerate(main_files, start=1):
        outs.append(process_one_output(
            main, i, out_dir, ffmpeg, registry, stop_event,
            head_pool=head_pool, tail_pool=tail_pool, cover_pool=cover_pool,
            head_count=head_count, tail_count=tail_count, cover_on=cover_on,
            threads=threads, log=log, algorithm=algorithm))
    return outs
