"""主合成命令与单条视频的参数组装。

filter_complex 拼装、首帧/主图选择、商品图掷骰、起始帧夹取。
改滤镜链后必须做像素级抽帧校验；输入索引要跟着「有没有商品图 / 有没有画中画」条件算，
写死 [2:v] 会把画中画轨当商品图叠上去且不报错。"""

import os
import random

from .constants import CRF, HW_STAGE_MAIN, MARGIN_BOTTOM, PRODUCT_CHANCE, PRODUCT_START_FRAME, PRODUCT_WIDTH_RATIO
from .ffmpeg_bin import FFMPEG
from .hwaccel import hw_decode_args
from .probe import _even, list_images, probe_image_size, probe_media

# ============================================================================
# 七、主合成命令
# ============================================================================

def build_command(src, opts, info, dst, pip=None):
    """
    构造最终合成的 ffmpeg 命令。

    滤镜拓扑（一路到底，只编码一次）：
        [1:v] 首帧图 ──┐
                       ├─ concat ─→ 主画面 ─┐
        [0:v] 原片第2帧起 ┘                    ├─ overlay 产品图（可选） ─ overlay 画中画（可选） ─→ 输出
        [2:v] 产品图 ─────────────────────────┘
        [3:v] 画中画轨 ───────────────────────────────────┘

    商品图与画中画都是**可选的**：商品图由"显示概率"决定（见 _decide_product），
    画中画由界面开关决定。谁缺席，谁那一路输入和那段 overlay 就整段不生成，
    后面的输入索引跟着前移（首图恒为 [1:v]，商品图 [2:v]，画中画 [2:v] 或 [3:v]）。

    :param src  : 主视频路径
    :param opts : 选项字典
    :param info : 主视频 probe 结果
    :param dst  : 输出路径
    :param pip  : (画中画文件, 宽, 高) 或 None
    返回 (命令列表, 产品图输出宽, 产品图输出高)；没有商品图时宽高都是 0
    """
    w, h, fps = info["width"], info["height"], info["fps_str"]
    # 商品图是**可选**的：概率为 0 或这一条掷骰未命中时，opts 里没有 product，
    # 直接把这道工序整段省掉（不叠透明图，也不多喂一路输入）。
    use_prod = bool(opts.get("product")) and bool(opts.get("prod_size"))

    # ---- 输入索引按实际用到的输入排 ----
    # [0]=主视频   [1]=首图   [2]=商品图（可选）   [2 或 3]=画中画（可选）
    # 必须跟着条件一起算：写死 [2:v] 会指到画中画轨上，成片会出现"商品图位置
    # 变成一片画中画"这种莫名其妙的错位。
    cover_i = 1
    prod_i = 2 if use_prod else None
    pip_i = (3 if use_prod else 2) if pip else None

    target_w = target_h = 0
    if use_prod:
        pw_src, ph_src = opts["prod_size"]
        # ---- 产品图目标尺寸：宽 = 视频宽 * 1/4，等比；过高时按可用高度回缩 ----
        target_w = _even(w * PRODUCT_WIDTH_RATIO)
        target_h = _even(ph_src * target_w / pw_src)
        max_h = max(2, h - MARGIN_BOTTOM * 2)
        if target_h > max_h:
            target_h = _even(max_h)
            target_w = _even(pw_src * target_h / ph_src)

    overlay_from = max(0, int(opts.get("prod_start", PRODUCT_START_FRAME)))   # 产品图从第几帧开始显示
    fc = (
        # 首帧：图片拉伸到主视频尺寸，只取 1 帧，帧率对齐
        f"[{cover_i}:v]scale={w}:{h},setsar=1,format=yuv420p,fps={fps},"
        f"trim=end_frame=1,setpts=PTS-STARTPTS[first];"
        # 剩余帧：主视频从第 2 帧开始
        f"[0:v]trim=start_frame=1,setpts=PTS-STARTPTS,setsar=1,format=yuv420p[rest];"
        f"[first][rest]concat=n=2:v=1:a=0[vcat]"
    )
    last = "[vcat]"
    if use_prod:
        fc += (
            # 产品图：缩放到目标尺寸，保留透明度
            f";[{prod_i}:v]scale={target_w}:{target_h},format=rgba,setsar=1[prod];"
            # 产品图 overlay：水平居中，距底部 MARGIN_BOTTOM
            f"{last}[prod]overlay=x=(W-w)/2:y=H-h-{MARGIN_BOTTOM}:"
            f"enable='gte(n,{overlay_from})'[v2]"
        )
        last = "[v2]"
    if pip:
        pip_file, pip_w, pip_h = pip
        # 位置随机微抖动（±N px），抗哈希；对观感几乎无影响
        j = max(0, int(opts["rnd_jitter"]))
        jx = random.randint(-j, j) if j else 0
        jy = random.randint(-j, j) if j else 0
        right = _even(w * opts["pip_right"]) + jx
        top = _even(h * opts["pip_top"]) + jy
        fc += (
            f";[{pip_i}:v]scale={pip_w}:{pip_h},setsar=1,fps={fps},format=yuv420p[pip];"
            # eof_action=repeat：若画中画轨比主视频短，定格最后一帧而不是消失
            f"{last}[pip]overlay=x=W-w-{right}:y={top}:eof_action=repeat[vout]"
        )
        last = "[vout]"
    else:
        fc += f";{last}null[vout]"
        last = "[vout]"

    # ---- 组装命令行 ----
    # 硬件解码只加在**视频**输入上（首图/主图是单张图，走硬解没意义还可能失败）。
    # 实测主合成阶段开不开硬解无差别，所以"自动"在这里不开，见 hw_decode_args。
    hw = hw_decode_args(opts.get("hwaccel"), HW_STAGE_MAIN)
    # -progress pipe:1：让 ffmpeg 把"已编码多少秒"逐行写到 stdout，界面据此显示
    # 真实进度（解析见 _read_out_time）。日志仍在 stderr，两者互不干扰。
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-nostats", "-progress", "pipe:1"]
    cmd += hw + ["-i", src]
    cmd += ["-i", opts["cover"]]
    if use_prod:
        cmd += ["-i", opts["product"]]
    if pip:
        cmd += hw + ["-i", pip[0]]
    cmd += ["-filter_complex", fc, "-map", last]
    if info["has_audio"]:
        cmd += ["-map", "0:a?"]
        # 音频：aac/mp3 直接复制，其它格式转 aac（避免容器不兼容）
        cmd += ["-c:a", "copy"] if all(c in ("aac", "mp3") for c in info["acodecs"]) \
            else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", opts["preset"], "-crf", str(CRF),
            "-pix_fmt", "yuv420p",
            "-threads", str(opts["threads"])]
    if info["duration"] > 0:
        cmd += ["-t", f"{info['duration']:.3f}"]     # 输出长度锁死为主视频长度
    cmd += ["-movflags", "+faststart", "-avoid_negative_ts", "make_zero", dst]
    return cmd, target_w, target_h


def _clamp_prod_start(opts, info, say=None):
    """
    把「产品图起始帧」夹到视频真实帧数范围内，返回一份**新的** opts（不改调用方那份）。

    背景：起始帧设成 60，而某个视频只有 30 帧（1 秒）时，overlay 的 enable 条件
    永远不成立，产品图整条都不会出现，用户会以为功能坏了。这里退到最后一帧并如实说明。

    :param opts: 本批共用的选项字典（多线程共享，**绝不能就地改**）
    :param info: probe_media 的结果
    :return: 原字典（无需调整）或调整后的副本
    """
    start = max(0, int(opts.get("prod_start", PRODUCT_START_FRAME)))
    fps = float(info.get("fps") or 0)
    dur = float(info.get("duration") or 0)
    frames = int(dur * fps) if fps > 0 and dur > 0 else 0
    if frames and start >= frames:
        fixed = max(0, frames - 1)
        if say:
            say(f"说明：产品图起始帧 {start} 超出这个视频的总帧数（{frames} 帧），"
                f"已从第 {fixed} 帧起显示")
        start = fixed
    if start == opts.get("prod_start"):
        return opts
    new = dict(opts)
    new["prod_start"] = start
    return new


def _decide_product(opts):
    """
    按「商品图显示概率」为本条视频掷骰，返回一份**新的** opts。

    概率语义（每条视频独立掷骰，不是整批抽签）：
        0   → 一定不叠加商品图
        100 → 一定叠加（从主图目录里随机抽一张）
        40  → 40% 的概率叠加

    未命中时把 `product_files` 置空（下游据此省掉一路输入、一段 overlay 和一次
    图片尺寸探测），而不是换一张透明图去叠——那样每帧仍要跑一次 alpha 混合。

    必须在 `_pick_images` **之前**调用，否则会白探一次用不到的图片尺寸。
    返回副本的原因与 `_pick_images` 相同：opts 被同批所有并发视频共享。
    """
    try:
        chance = int(opts.get("prod_chance", PRODUCT_CHANCE))
    except Exception:
        chance = PRODUCT_CHANCE
    chance = max(0, min(100, chance))
    if chance >= 100:                       # 常见情况：不掷骰子，省掉一次随机数
        return opts
    if chance > 0 and random.random() * 100 < chance:
        return opts
    new = dict(opts)
    new["product_files"] = []               # 这条视频不走商品图分支
    new["product"] = ""
    new["prod_size"] = None
    return new


def _pick_images(opts):
    """
    为**当前这条视频**随机固定首图与主图，返回一份新的 opts。

    需求：首图和主图各自放在一个目录里，每条视频从目录里随机各挑一张；
    同一条视频全程只用挑中的那两张——中途不再换图，否则观众能看出来。

    只传单个文件时等价于"目录里只有这一张"，行为与以前完全一致。
    首图与主图**各自独立**处理：商品图概率为 0 时主图目录会是空的，
    这时首图仍然要照常抽（首帧图是成片的必要拼图，与商品图无关）。

    注意返回的是**副本**：opts 由多个并发视频共享，绝不能就地修改。
    两边都取不到图时等价于原样返回，由上游的参数校验负责提示。

    挑中的文件名记在返回值的 picked_cover / picked_product 里，
    由调用方拼进"完成"那一行——这样每条视频只占一行日志，信息也不丢。
    """
    covers = opts.get("cover_files") or list_images(opts.get("cover"))
    prods = opts.get("product_files") or list_images(opts.get("product"))
    if not covers and not prods:
        return opts

    new = dict(opts)
    if covers:
        new["cover"] = random.choice(covers)
        new["picked_cover"] = os.path.basename(new["cover"])
    if prods:
        new["product"] = random.choice(prods)
        try:
            new["prod_size"] = probe_image_size(new["product"])
        except Exception:
            new["prod_size"] = opts.get("prod_size")   # 读不到尺寸就沿用外面给的
        new["picked_product"] = os.path.basename(new["product"])
    elif opts.get("product") and not os.path.isfile(opts.get("product") or ""):
        # 目录里一张可用图片都没有：宁可这条不叠商品图，也不能把"目录路径"
        # 当成图片喂给 ffmpeg（那会是一条看不懂的输入错误）
        new["product"] = ""
        new["prod_size"] = None
    return new


def _safe_probe(path):
    """探测视频信息，失败返回 None（预检阶段用，绝不因为单个文件异常打断整批）"""
    try:
        return probe_media(path)
    except Exception:
        return None
