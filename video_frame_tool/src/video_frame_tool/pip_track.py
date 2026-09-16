"""画中画素材轨构建。

片段规划 → 批量编码 → stream copy 拼接。
**色彩空间必须统一**：_clip_filter 的每个 scale 都要带 out_color_matrix=bt709，
链末 setparams 只是标记、不能代替真正的像素矩阵转换（只加标记会让 601 素材偏色）。"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import os
import random

from .constants import CRF, HW_STAGE_PIP_RAW, HW_STAGE_PIP_SMALL, MAX_SEGMENTS, PIP_BATCH, PIP_BATCH_PRESET, PIP_BATCH_THREADS, SEG_MAX, SEG_MIN
from .ffmpeg_bin import FFMPEG
from .hwaccel import hw_decode_args
from .probe import _even
from .proc import _check_stopped, _run
from .small_pool import _is_small_copy, _small_short_side, prepare_small_pool, scan_pip_pool

# ============================================================================
# 六、画中画素材轨构建
# ============================================================================

def _clip_filter(pw, ph, zoom, flip_h, color_on, color_params, fps_str, crop_fill, speed=1.0):
    """
    生成单个画中画片段的视频滤镜链（字符串）。

    处理顺序说明：
        0) setpts             —— 变速（PTS/speed，配合前面的 -t 实现"加速播放"）
        1) hflip              —— 随机水平镜像
        2) 统一尺寸 + 居中裁剪 —— zoom 时会先放大再裁回，产生像素位移
        3) eq 调色             —— 亮度/对比度/饱和度的极小扰动
        4) fps / setsar / format —— 与主视频对齐，保证可拼接

    :param pw, ph      : 目标画中画尺寸
    :param zoom        : 缩放系数（>=1.0；1.0 表示不缩放）
    :param flip_h      : 是否水平翻转
    :param color_on    : 是否启用随机调色
    :param color_params: (brightness, contrast, saturation) 三元组
    :param fps_str     : 主视频帧率字符串
    :param crop_fill   : True=裁剪填满；False=保持比例加黑边
    :param speed       : 变速倍数，1.0 表示不变速
    """
    parts = []
    # ---- 色彩统一：素材池里混着 bt709 / bt2020(HLG) / bt470bg(601) 三种。
    # 它们被拼到同一条画中画轨以后，ffmpeg 播到色彩切换点就会
    # "Reconfiguring filter graph because video parameters changed"，
    # 滤镜图一重配置，主合成的帧时间戳就错乱——实测成片视频轨只剩源的一半
    # （日志 drop=3643~6296 帧），播到两分钟画面卡死、只剩声音。
    # 所以这里在缩放的同时把像素**真正转换**到 bt709（不是只改标记，
    # 只改标记会让 601 素材偏色），末尾再用 setparams 把标记也统一。
    cm = ":out_color_matrix=bt709"
    # 统一时间基准：无论从哪里截取，都让片段从 0 时刻开始，拼接时才不会出现空隙。
    # 变速（加速）就是在这里实现的：PTS 除以倍数，播放器就会用更短的时间放完同样的画面。
    parts.append(f"setpts=(PTS-STARTPTS)/{max(0.01, speed):.6f}")
    if flip_h:
        parts.append("hflip")

    # ---- 尺寸统一：无论走哪条分支，最后必然得到 pw×ph 的画面 ----
    # 这一点不能省：
    #   1) 所有片段尺寸必须完全一致，否则后面无法拼接；
    #   2) h264 要求宽高都是偶数，而"等比放大到覆盖目标"会算出奇数边
    #      （例：3840x2160 的素材填 258x384 → 683x384，683 是奇数 → 编码器直接报错）。
    #      所以放大之后必须 crop 回目标尺寸；走"完整显示"模式则用 pad 补齐。
    if crop_fill:
        if zoom > 1.0001:
            # 随机缩放：先放大到略大于目标，再裁回，画面像素发生位移（抗查重）
            parts.append(f"scale={_even(pw * zoom)}:{_even(ph * zoom)}"
                         f":force_original_aspect_ratio=increase{cm}")
        else:
            parts.append(f"scale={pw}:{ph}:force_original_aspect_ratio=increase{cm}")
        parts.append(f"crop={pw}:{ph}")
    else:
        parts.append(f"scale={pw}:{ph}:force_original_aspect_ratio=decrease{cm}")
        parts.append(f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:black")

    if color_on:
        b, c, s = color_params
        parts.append(f"eq=brightness={b:.4f}:contrast={c:.4f}:saturation={s:.4f}")

    parts += [f"fps={fps_str}", "setsar=1", "format=yuv420p",
              # 与上面的色彩统一配套：把标记也写成 bt709
              "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"]
    return ",".join(parts)


def _plan_pip_segments(main, opts, pool, target=None):
    """
    规划画中画片段（纯计算，不启动任何 ffmpeg，毫秒级完成）。

    规则：
        - 素材洗牌后依次取用，**同一轮内不重复**；用尽则重新洗牌循环复用
        - 每段在"掐头去尾后的有效区间"内随机取起点、随机时长
        - 每段独立随机镜像 / 缩放 / 调色
        - 直到累计时长够目标长度为止

    时长换算：成片贡献时长 = 截取素材长度 ÷ 加速倍数，故 need_src = out_seg * speed

    :param target: 需要凑够的总时长（秒）；默认取主视频时长。
                   有片段因素材损坏被丢弃时，调用方会传一个补足值再规划一轮。
    :return: (specs, reused, pw, ph)
             specs  : [{"path","start","need_src","out_seg","vf"}, ...]，顺序即最终播放顺序
             reused : 是否发生过"素材用尽后循环复用"
    """
    T = main["duration"] if target is None else float(target)
    w, h, fps = main["width"], main["height"], main["fps_str"]
    pw = _even(w * opts["pip_w"])             # 画中画实际像素尺寸
    ph = _even(h * opts["pip_h"])
    crop_fill = opts["pip_fill"] == "crop"
    speed = opts["pip_speed"]
    head, tail = opts["pip_head"], opts["pip_tail"]

    pool = list(pool)                  # 缓存列表由并发任务共享，只打乱本次的顺序
    random.shuffle(pool)
    total, specs, reused, i = 0.0, [], False, 0

    while total < T - 0.05 and len(specs) < MAX_SEGMENTS:
        # ---- 素材轮换：用尽则重新洗牌（尽量把重复间隔拉大） ----
        if i >= len(pool):
            random.shuffle(pool)
            i = 0
            reused = True
        item = pool[i]
        i += 1

        src_dur = item["dur"]

        # ---- 掐头去尾：算出素材的"有效区间" ----
        lo = src_dur * head                   # 有效起点（默认 10% 处）
        hi = src_dur * (1.0 - tail)           # 有效终点（默认 90% 处）
        usable = hi - lo
        if usable < 0.5:                      # 素材太短，退化为整段可用
            lo, usable = 0.0, src_dur

        # ---- 决定这一段要"贡献"多长成片时间，并换算成需要截取的素材长度 ----
        remain = T - total
        out_seg = min(random.uniform(SEG_MIN, SEG_MAX), remain)
        need_src = min(out_seg * speed, usable)
        out_seg = need_src / speed            # 受素材长度限制后的实际贡献时长
        if out_seg < 0.05:                    # 兜底，避免产生 0 长度片段
            break

        # ---- 在有效区间内随机取起点，保证不越界 ----
        start = lo + random.uniform(0.0, max(0.0, usable - need_src - 0.05))

        # ---- 随机化参数（逐段独立随机，抗平台查重） ----
        zoom = random.uniform(1.0, opts["rnd_zoom"]) if opts["rnd_zoom"] > 1.0001 else 1.0
        color_params = (
            random.uniform(-0.020, 0.020),    # brightness 亮度微扰
            random.uniform(0.980, 1.020),     # contrast   对比度微扰
            random.uniform(0.980, 1.020),     # saturation 饱和度微扰
        )
        vf = _clip_filter(pw, ph, zoom,
                          opts["rnd_flip_h"] and random.random() < 0.5,
                          opts["rnd_color"], color_params,
                          fps, crop_fill, speed)

        specs.append({"path": (opts.get("small_map") or {}).get(item["path"])
                              or item["path"],
                      "start": start, "need_src": need_src,
                      "out_seg": out_seg, "vf": vf})
        total += out_seg

    return specs, reused, pw, ph


def _encode_pip_batch(batch, dst, registry, hw=None):
    """
    用一个 ffmpeg 进程把一批片段拼成一个文件（多输入 + concat 滤镜）。

    这是画中画性能的核心：早期实现是"一段一个进程、串行跑"，
    9 分钟视频要启动 100+ 次 ffmpeg，进程启停开销比编码本身还大。
    现在每批 8 段只起 1 个进程，批内由 ffmpeg 自动并行解码，批次之间再并行。

    命令行形态（每段：定位 → 读一小段 → 各自套滤镜 → 顺序拼接）：
        -ss 起点 -t 长度 -i 素材1  -ss … -t … -i 素材2  …
        -filter_complex "[0:v]滤镜[v0];[1:v]滤镜[v1];…;[v0][v1]…concat=n=N:v=1:a=0[o]"
        -map [o] -an -c:v libx264 … batch.mp4

    :param batch   : _plan_pip_segments 产出的片段规格列表（本批）
    :param dst     : 本批输出文件
    :param registry: 进程注册表（用于"停止"时终止 ffmpeg）
    :param hw      : 硬件加速模式（见 hw_decode_args）。这一批解的是 480p 副本，
                     实测开硬解反而慢 60%，所以"自动"在这里不会开（真正会用硬解的
                     是生成副本那一步）；"开启"才会强行开。
    """
    accel = hw_decode_args(hw, HW_STAGE_PIP_SMALL
                           if (batch and all(_is_small_copy(sp["path"]) for sp in batch))
                           else HW_STAGE_PIP_RAW)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    for sp in batch:
        # -ss / -t 写在 -i 之前 = 输入选项：只解码需要的那一小段，速度最快
        cmd += accel + ["-ss", f"{sp['start']:.3f}", "-t", f"{sp['need_src']:.3f}",
                        "-i", sp["path"]]

    branches = "".join(f"[{i}:v]{sp['vf']}[s{i}];" for i, sp in enumerate(batch))
    inputs = "".join(f"[s{i}]" for i in range(len(batch)))
    fc = f"{branches}{inputs}concat=n={len(batch)}:v=1:a=0[o]"

    cmd += ["-filter_complex", fc, "-map", "[o]",
            "-an",                                     # 画中画永远静音
            "-c:v", "libx264", "-preset", PIP_BATCH_PRESET, "-crf", str(CRF),
            "-pix_fmt", "yuv420p",
            # 与 _clip_filter 末尾的 setparams 配套：把色彩标记写进容器/SPS，
            # 这样 concat demuxer 用 -c copy 拼出来的整条画中画轨都是纯 bt709，
            # 不会再出现 HDR/bt709 交替导致主合成重配置滤镜图（详见 _clip_filter）。
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-threads", str(PIP_BATCH_THREADS),
            dst]
    _run(cmd, registry)


def _encode_pip_batches(specs, tmpdir, opts, registry, log, prog=None):
    """
    分批编码所有片段，批次之间并行执行（并行度由 opts["pip_parallel"] 决定）。

    并行度在 _start 里按"CPU 核心数 ÷ 实际同时处理的视频数"算好，
    保证多个视频同时处理时不会把核心全部抢光。

    容错：某个批次失败时（个别素材损坏、格式异常等），不再整批放弃，
    而是把该批拆成单段逐个重试，只丢弃真正跑不通的那几段，并把它们回传给调用方，
    由调用方补足时长——这样一条坏素材不会让整个视频丢掉画中画。

    :return: (batch_files, failed_specs)
             failed_specs 里是没编成功的片段（调用方据此补时长）
    """
    batches = [specs[i:i + PIP_BATCH] for i in range(0, len(specs), PIP_BATCH)]
    files = [os.path.join(tmpdir, f"b{i:03d}.mp4") for i in range(len(batches))]
    parallel = max(1, min(len(batches), int(opts.get("pip_parallel", 1) or 1)))
    hw = opts.get("hwaccel")

    done, failed = 0, []

    def note(n):
        """批次进度只喂进度条，不写日志（用户要求：不要一行一行刷屏）"""
        if prog is not None:
            prog.count("pip", n, len(batches))   # 真实批次数 → 真实进度

    def run_batch(batch, path, bid):
        _check_stopped(opts)
        try:
            _encode_pip_batch(batch, path, registry, hw)
            return []
        except Exception as e:
            _check_stopped(opts)
            # 整批失败 → 拆开逐段重试，定位到具体是哪几段有问题
            log(f"   有素材无法编码，正在逐段排查（{e}）")
            if os.path.exists(path):
                os.remove(path)       # 失败残留不能混进后面的 stream copy
            ok = []
            bad = []
            for i, sp in enumerate(batch):
                _check_stopped(opts)
                seg_path = os.path.join(tmpdir, f"fix_{bid:03d}_{i:02d}.mp4")
                try:
                    _encode_pip_batch([sp], seg_path, registry, hw)
                    ok.append(seg_path)
                except Exception:
                    _check_stopped(opts)
                    bad.append(sp)
            if len(ok) == 1:
                os.replace(ok[0], path)          # 只剩一段时直接改名复用，省一次拼接
            elif ok:
                _concat_pip_batches(ok, path, registry)
            return bad

    if parallel == 1 or len(batches) == 1:
        for bid, (batch, path) in enumerate(zip(batches, files)):
            failed += run_batch(batch, path, bid)
            done += 1
            note(done)
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futures = {ex.submit(run_batch, b, p, bid): p for bid, (b, p) in enumerate(zip(batches, files))}
            for fut in as_completed(futures):
                failed += fut.result()
                done += 1
                note(done)

    # 整批和逐段都没救回来的批次，文件不存在 → 从结果里剔除，避免拼接时报错
    return [f for f in files if os.path.exists(f) and os.path.getsize(f) > 0], failed


def _concat_pip_batches(files, dst, registry):
    """
    把各批文件顺序拼成完整画中画轨。

    各批由同一套参数（同尺寸/帧率/像素格式）编码，因此可以直接 stream copy 拼接，
    不需要再解码重编码（秒级完成）。
    """
    if len(files) == 1:
        return files[0]
    listfile = os.path.join(os.path.dirname(files[0]), "list.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for path in files:
            # 路径统一写成正斜杠：concat demuxer 把反斜杠当转义符，
            # Windows 下的 "C:\...\b000.mp4" 会被解析坏掉（报 No such file）。
            # ffmpeg 在 Windows 上也接受正斜杠，所以两种系统都安全。
            safe = path.replace("\\", "/").replace("'", "'\\''")
            fh.write("file '%s'\n" % safe)
    _run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
          "-f", "concat", "-safe", "0", "-i", listfile, "-an", "-c", "copy", dst], registry)
    return dst


def build_pip_track(main, opts, tmpdir, registry, log, prog=None):
    """
    为一条主视频生成画中画轨（静音），返回 (文件路径, 宽, 高)。

    三段式流程（每步都很轻）：
        1. 规划：算出所有片段（纯计算，不碰 ffmpeg）
        2. 编码：按 PIP_BATCH 分批，每批一个 ffmpeg 进程，批次并行
        3. 拼接：各批 stream copy 成一条轨

    :param main    : 主视频的 probe_media 结果
    :param opts    : 全局选项字典（见 App._start）
    :param tmpdir  : 临时工作目录（调用方负责清理）
    :param registry: 进程注册表
    :param log     : 日志回调
    :param prog    : VideoProgress（进度条用，可空）；这里喂它副本数与批次数
    """
    pool = scan_pip_pool(opts["pip_dir"], log)
    if not pool:
        raise RuntimeError(f"素材目录里没有可用的小视频：{opts['pip_dir']}")

    specs, reused, pw, ph = _plan_pip_segments(main, opts, pool)
    if not specs:
        raise RuntimeError("没有规划出任何画中画片段")

    # ---- 素材低清副本（实测最大的提速项，理由见 PIP_SMALL_DIR 上方注释） ----
    # 画中画在成片里只占约 260x384 像素，却要解码 1080p 素材——这是画中画阶段
    # 93% 的时间与 86% 的内存开销所在。先降成小尺寸副本再拼接，观感毫无差别。
    #
    # 副本是**长期缓存**：已经生成过的会被直接认出来复用（只核对，不转码），
    # 只有"没生成过 / 素材被换过 / 画中画尺寸调大了"的才会新建。见 prepare_small_pool。
    #
    # 副本映射通过本视频私有的 opts["small_map"] 传给 _plan_pip_segments。
    # 绝不能写成"挂到 pool 里的素材字典上"——那份 pool 是进程内缓存的、还被其它
    # 并发视频共享，改了会串（踩过的坑：第二条视频开始 specs 直接指向副本，
    # used 过滤成空集，于是副本核对被整个跳过）。
    opts = dict(opts)                  # 本视频私有副本，避免往共享字典里写东西
    small = {}
    if opts.get("pip_small", True):
        used = {sp["path"] for sp in specs}

        def copy_note(n, m):
            """副本进度按真实个数推进度条"""
            if prog is not None:
                prog.count("copy", n, m)

        small = prepare_small_pool(
            [it for it in pool if it["path"] in used],
            _small_short_side(main["width"], main["height"], opts),
            log, opts.get("stop_event"), hw=opts.get("hwaccel"),
            progress_cb=(copy_note if prog else None)) or {}
        opts["small_map"] = small

    # 规划发生在建副本之前，所以要把已经规划好的片段改指向副本；
    # 后面"兜底补段"会重新规划一轮，那时会直接通过 small_map 拿到副本路径。
    for sp in specs:
        dst = small.get(sp["path"])
        if dst:
            sp["path"] = dst

    batches = (len(specs) + PIP_BATCH - 1) // PIP_BATCH
    log(f"   准备画中画：从 {len(pool)} 个素材里抽取 {len(specs)} 段，"
        f"共 {batches} 批" + ("（素材不够，已循环复用）" if reused else "（素材不重复）"))

    files, failed = _encode_pip_batches(specs, tmpdir, opts, registry, log, prog)

    # ---- 兜底补段：个别素材坏了、编不出来时，用别的素材把缺掉的时长补回来 ----
    # 最多补两轮，避免极端情况下反复重试。补出来的段直接接在末尾——
    # 画中画本来就是随机顺序，接在后面不影响观感。
    for attempt in range(2):
        if not failed:
            break
        missing = sum(sp.get("out_seg", 0.0) for sp in failed)
        log(f"   有 {len(failed)} 段素材不可用，正在用其它素材补上这 {missing:.1f} 秒")
        if missing < 0.1:
            break
        extra, _, _, _ = _plan_pip_segments(main, opts, pool, target=missing + 0.3)
        if not extra:
            break
        patch_dir = os.path.join(tmpdir, f"patch{attempt}")
        os.makedirs(patch_dir, exist_ok=True)
        more_files, failed = _encode_pip_batches(extra, patch_dir, opts, registry, log, prog)
        files += more_files
        # 补段如果也失败，下一轮继续补；两轮之后仍失败的就不再纠缠
        if attempt == 1 and failed:
            log(f"   {len(failed)} 段素材始终无法使用，已跳过")

    if not files:
        raise RuntimeError("所有画中画素材都无法编码")
    pip_file = _concat_pip_batches(files, os.path.join(tmpdir, "pip.mp4"), registry)
    return pip_file, pw, ph
