"""画中画素材池与低清副本（磁盘缓存 + 并行扫描 + 后台预热）。

**副本是长期缓存，任何代码路径都不许删除**；改副本产出要 PIP_SMALL_V += 1，
改探测记录结构要 _POOL_CACHE_V += 1。所有调用走 prepare_small_pool()（带目录锁）。
不要把字段写进 scan_pip_pool() 返回的 pool 字典——它是被同批视频共享的进程内缓存。"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import threading
import time

from .constants import PIP_SMALL_CRF, PIP_SMALL_DIR, PIP_SMALL_GOP, PIP_SMALL_HEADROOM, PIP_SMALL_META, PIP_SMALL_MIN_SHORT, PIP_SMALL_PRESET, PIP_SMALL_ROSTER, PIP_SMALL_THREADS, PIP_SMALL_V, SCAN_THREADS
from .ffmpeg_bin import FFMPEG
from .hwaccel import _small_accel_args
from .probe import _even, _list_videos, probe_dur_size, probe_duration

# ============================================================================
# 五、画中画素材池（带磁盘缓存 + 并行扫描）
# ============================================================================

def _pool_cache_path(folder):
    """素材池缓存文件路径（放在素材目录内的隐藏文件）"""
    return os.path.join(folder, ".pip_cache.json")


# 进程内素材池缓存：folder -> (目录指纹, 素材列表)
# 作用：多个并发任务同时启动时，只有第一个真正扫描，其余直接复用内存结果
_POOL_MEM = {}
# 素材池记录的字段版本。加字段（如 v2 的宽高、v3 的旋转换算）时把它 +1，
# v4 将文件指纹的修改时间改为纳秒；旧元数据会重扫一次，不重建低清视频副本。
_POOL_CACHE_V = 4
_POOL_LOCKS = {}          # folder -> 扫描互斥锁（"单个飞行中"语义）
_POOL_LOCK = threading.Lock()


def _folder_lock(folder):
    """取某个素材目录专属的扫描锁（首次调用时惰性创建）"""
    with _POOL_LOCK:
        lk = _POOL_LOCKS.get(folder)
        if lk is None:
            lk = _POOL_LOCKS[folder] = threading.Lock()
        return lk


def _folder_signature(files):
    """
    计算目录指纹：文件名 → (大小, mtime)。
    只要文件增删或改动，指纹就会变化，缓存自动失效。
    """
    sig = {}
    for f in files:
        try:
            st = os.stat(f)
            sig[os.path.basename(f)] = [st.st_size, st.st_mtime_ns]
        except OSError:
            continue
    return sig


def scan_pip_pool(folder, log=None):
    """
    扫描画中画素材目录，返回 [{"path": 绝对路径, "dur": 时长秒}, ...]。

    性能要点（改动请谨慎）：
      - 进程内缓存：同一批任务只扫描一次（并发时靠锁保证不重复劳动）
      - 首次扫描：16 路线程池并行 probe（400 个素材约 1 秒）
      - 结果连同目录指纹写入 .pip_cache.json，二次启动毫秒级命中
      - 指纹使用体积和纳秒修改时间；目录变化只探测新增/修改的文件
      - 完全命中时不重写台账；删除的素材从新列表中移除

    :param folder: 素材目录
    :param log:    日志回调（可空）
    """
    files = _list_videos(folder)
    if not files:
        return []
    sig = _folder_signature(files)

    # --- 进程内缓存（并发任务共享，最快的路径） ---
    with _POOL_LOCK:
        mem = _POOL_MEM.get(folder)
    if mem and mem[0] == sig:
        return mem[1]

    # --- 加锁：确保同一时刻只有一个任务在扫描该目录，其余等结果 ---
    with _folder_lock(folder):
        with _POOL_LOCK:
            mem = _POOL_MEM.get(folder)
        if mem and mem[0] == sig:
            return mem[1]

        # 目录有变化时仍复用未改动文件的记录；只探测新增/变更的素材。
        cached = {"v": _POOL_CACHE_V, "signature": mem[0], "items": mem[1]} if mem else {}
        cache_file = _pool_cache_path(folder)
        if not cached:
            try:
                with open(cache_file, "r", encoding="utf-8") as fh:
                    cached = json.load(fh)
            except (OSError, ValueError):
                cached = {}
        if (not isinstance(cached, dict) or cached.get("v") != _POOL_CACHE_V
                or not isinstance(cached.get("signature"), dict)
                or not isinstance(cached.get("items"), list)):
            cached = {}
        old_sig = cached.get("signature") or {}
        file_set = set(files)
        reusable = {it["path"]: it for it in cached.get("items", [])
                    if isinstance(it, dict) and isinstance(it.get("path"), str)
                    and it["path"] in file_set
                    and isinstance(it.get("dur"), (int, float)) and it["dur"] > 0.6
                    and old_sig.get(os.path.basename(it["path"]))
                    == sig.get(os.path.basename(it["path"]))}

        # --- 并行探测 ---
        t0 = time.time()
        reused_count = len(reusable)

        def _one(path):
            try:
                d, w, h = probe_dur_size(path)
                if d > 0.6:
                    return {"path": path, "dur": float(d), "w": int(w), "h": int(h)}
            except Exception:
                pass
            return None

        pending = [path for path in files if path not in reusable]
        if pending:
            with ThreadPoolExecutor(max_workers=min(SCAN_THREADS, len(pending))) as ex:
                for r in ex.map(_one, pending):
                    if r:
                        reusable[r["path"]] = r
        items = [reusable[path] for path in files if path in reusable]

        if log and pending:
            log(f"已读取 {len(pending)} 个新增/变更素材，复用 {reused_count} 个缓存"
                f"（用时 {time.time() - t0:.1f} 秒）")

        with _POOL_LOCK:
            _POOL_MEM[folder] = (sig, items)

        if cached.get("signature") == sig and not pending:
            return items
        # --- 写缓存（失败不影响主流程） ---
        try:
            with open(cache_file, "w", encoding="utf-8") as fh:
                json.dump({"v": _POOL_CACHE_V, "signature": sig, "items": items}, fh)
        except Exception:
            pass
        return items


def _run_quiet(cmd, timeout=900):
    """
    跑一条命令，只要退出码（不注册进 registry、不抛异常）。

    专门给"素材加速副本"这类后台预处理用：单个素材失败不该打断整批，
    也不该混进底部"正在转码 N 个"的计数里（那个计数是给成片编码看的）。
    """
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode
    except Exception:
        return 1


def _small_short_side(main_w, main_h, opts):
    """
    算出素材低清副本的"短边"该取多少像素。

    目标：副本被裁成画中画之后，像素不能比实际显示尺寸还小，否则会糊。
        需要的像素 = 主视频尺寸 × 画中画占比 × 随机缩放上限 × 安全余量
    """
    zoom = max(1.0, float(opts.get("rnd_zoom", 1.0)))
    need = max(_even(main_w * opts["pip_w"]), _even(main_h * opts["pip_h"]))
    return max(PIP_SMALL_MIN_SHORT, _even(need * zoom * PIP_SMALL_HEADROOM))


def _is_small_copy(path):
    """这个素材路径是不是"低清副本"（就在 .pip_small/ 里）"""
    return os.path.basename(os.path.dirname(path)) == PIP_SMALL_DIR


def _build_small_copy(item, dst, short_side, hw=None):
    """
    生成单个素材的低清副本（供画中画拼接使用）。

    尺寸策略：按素材方向缩放**短边**（横屏缩高度、竖屏缩宽度），
    并且**只缩小、绝不放大**——放大只会增加解码量，不会多出任何信息。

    其它要点：
      - 去掉音轨（画中画全程静音，留着白占空间）
      - 关键帧间隔压到 PIP_SMALL_GOP，定位更快、切出来的起点更准
      - 先写 .part 临时文件，校验通过后才原子改名，
        避免"生成了半个文件、下次被当成可用副本"
      - 校验副本时长与原素材一致（不一致说明编码异常，宁可回退用原片）
    """
    w, h = int(item.get("w") or 0), int(item.get("h") or 0)
    vf = None
    if w and h:
        if w >= h:
            if h > short_side:
                vf = f"scale=-2:{short_side}"
        elif w > short_side:
            vf = f"scale={short_side}:-2"
    else:
        vf = f"scale=-2:{short_side}"

    # 临时文件名必须以 .mp4 结尾：ffmpeg 靠扩展名推断输出封装格式，
    # 写成 "xxx.mp4.part" 会直接报 "Unable to choose an output format"。
    tmp = dst + ".part.mp4"

    def make(accel):
        """拼一条完整的转码命令；accel 决定解码侧怎么走"""
        cmd = ([FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
               + accel + ["-i", item["path"]])
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-an", "-c:v", "libx264", "-preset", PIP_SMALL_PRESET,
                "-crf", str(PIP_SMALL_CRF), "-g", str(PIP_SMALL_GOP),
                "-pix_fmt", "yuv420p", "-threads", "2",
                "-movflags", "+faststart", tmp]
        return cmd

    def drop_tmp():
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    try:
        if _run_quiet(make(_small_accel_args(hw))) != 0:
            drop_tmp()
            # 硬解不可用（老机器）或该素材硬解失败 → 退回软解再试一次
            if _run_quiet(make(["-threads", "1"])) != 0:
                return
        d2 = probe_duration(tmp)
        if abs(d2 - float(item["dur"])) > max(0.3, float(item["dur"]) * 0.02):
            return
        os.replace(tmp, dst)
        return True
    except Exception:
        pass
    finally:
        drop_tmp()


def _stat_pair(path):
    """
    返回 (体积, 修改时间秒)。取不到就返回 (-1, -1)。

    台账靠这一对数字判断"源素材有没有被换过、副本有没有被外部改动过"，
    只 stat 不读文件内容，几百个素材也就几毫秒。
    """
    try:
        st = os.stat(path)
        return int(st.st_size), int(st.st_mtime)
    except OSError:
        return -1, -1


def small_dir_of(pip_dir):
    """素材目录 → 副本目录（两个地方都要用，集中一处免得写歪）"""
    return os.path.join(pip_dir, PIP_SMALL_DIR)


def _read_small_meta(small_dir):
    """
    读副本台账（副本目录里的 _meta.json）。

    台账只用来判断"要不要重建"，读不到 / 内容坏了都当空台账，
    顶多让下一次白重做一遍副本，绝不影响出片。
    """
    try:
        with open(os.path.join(small_dir, PIP_SMALL_META), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if isinstance(meta, dict) and isinstance(meta.get("items"), dict):
            meta.setdefault("v", PIP_SMALL_V)
            return meta
    except Exception:
        pass
    return {"v": PIP_SMALL_V, "items": {}}


def _write_small_meta(small_dir, meta):
    """原子写台账：先写 .tmp 再改名，避免写一半断电留下坏 JSON"""
    if meta == _read_small_meta(small_dir):
        return
    tmp = os.path.join(small_dir, PIP_SMALL_META + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(small_dir, PIP_SMALL_META))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _ensure_small_roster(small_dir):
    """在副本目录里放一份说明：这些文件是什么、会长期保留、删了会怎样"""
    p = os.path.join(small_dir, PIP_SMALL_ROSTER)
    if os.path.exists(p):
        return
    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "这个目录里放的是「画中画素材的低清副本」，是自动生成的加速缓存。\n"
                "\n"
                "为什么要有它：画中画在成片里只占约 260x384 像素，却要解码 1080p/4K 原片，\n"
                "画中画阶段的 93% 时间和 86% 内存都耗在这上面。先降成小尺寸副本再拼接，\n"
                "观感毫无差别，速度却能快十几倍。\n"
                "\n"
                "重要：这些副本**生成一次后会一直留着**，下次处理视频会直接复用，\n"
                "所以第二次开始几乎不用等。请不要手动删单个文件，也不要往这里放自己的东西。\n"
                "\n"
                "整个目录可以随时删掉——删了不会丢东西，工具下次会按需重新生成。\n"
                "（在「设置」里关掉加速副本，或直接删掉本目录，工具就会改用原素材。）\n"
            )
    except Exception:
        pass


def small_cache_stats(pip_dir):
    """
    只做 listdir + stat 的副本清点，返回 (已有副本数, 素材数, 副本总体积)。

    用于启动时的一句话汇报（"副本已经生成好了，会直接复用"）。
    刻意不读台账、不探测尺寸——启动阶段绝不能为了报个数去吃 CPU。
    """
    try:
        small = small_dir_of(pip_dir)
        have = size = 0
        if os.path.isdir(small):
            for f in os.listdir(small):
                if f.lower().endswith(".mp4"):
                    n = _stat_pair(os.path.join(small, f))[0]
                    if n > 4096:
                        have += 1
                        size += n
        return have, len(_list_videos(pip_dir)), size
    except Exception:
        return 0, 0, 0


def _small_names(pool, small_dir):
    """
    给每个素材定一个副本文件名，返回 {源素材: 副本路径}。

    默认直接用素材主名（xxx_raw.mp4 → xxx_raw.mp4），好认也好排错。
    只有当目录里出现"主名相同、扩展名不同"的素材（a.mp4 和 a.mov）时，
    才给它们加序号——否则两个素材会共用同一个副本文件，画中画会莫名其妙串素材。
    """
    dup = {}
    for it in pool:
        stem = os.path.splitext(os.path.basename(it["path"]))[0].lower()
        dup[stem] = dup.get(stem, 0) + 1
    used, out = {}, {}
    for it in pool:
        src = it["path"]
        stem = os.path.splitext(os.path.basename(src))[0]
        key = stem.lower()
        if dup.get(key, 1) > 1:
            used[key] = used.get(key, 0) + 1
            stem = f"{stem}.{used[key]}"
        out[src] = os.path.join(small_dir, stem + ".mp4")
    return out


def _small_plan(pool, small_dir, short_side):
    """
    把素材池分成「已有现成副本，直接调用」和「需要新建」两堆。全程只 stat，不解码。

    判定一个副本可以"直接调用"必须**同时**满足下面全部条件（差一条就重建）：

      1. 台账里有它的记录，且规格版本一致（工具升级改了副本规格后不会继续用旧的）
      2. 台账记的正是这个源素材（防止同名素材互相顶掉对方的副本）
      3. 源素材的体积 + 修改时间没变过（素材被替换过就失效）
      4. 副本文件的体积 + 修改时间与台账一致（被截断 / 被外部改动过就失效）
      5. 生成时用的短边 ≥ 这次需要的短边（后来把画中画调大了就得重做，否则会糊）

    另外兼容一种情况：副本是**以前版本生成、还没登记进台账**的。
    只要文件在、体积正常、且比源素材新，就直接认下来并补登记——
    否则用户一升级就会被逼着把几百个副本全部重做一遍，白白等好几分钟。

    **认领只发生在"台账里压根没有这条记录"时**（也就是纯粹的版本升级迁移）。
    如果台账里有记录但对不上（素材改了 / 尺寸要更大 / 副本被删了），一律重建——
    不能让认领逻辑把这些失效判断悄悄掩盖掉，否则调大画中画后永远用不到高清副本。

    :return: (mapping, todo, meta)
             mapping —— {源素材: 目标副本路径}（全部素材）
             todo    —— [(素材, 目标路径), ...]，空列表 = 全部已生成，直接调用
             meta    —— 台账（含本次补登记的条目，由调用方负责写回）
    """
    if not pool:
        return {}, [], _read_small_meta(small_dir)
    paths = {it["path"] for it in pool}
    # 编号沿用整目录的既有规则，不能随本次随机抽中的子集变化。
    names = paths | set(_list_videos(os.path.dirname(pool[0]["path"])))
    all_names = _small_names([{"path": path} for path in sorted(names)], small_dir)
    mapping = {path: all_names[path] for path in paths}
    meta = _read_small_meta(small_dir)
    ver_ok = meta.get("v") == PIP_SMALL_V
    if not ver_ok:
        # 规格升级后发布新版本；未登记的旧文件也不能再按历史副本认领。
        meta = {"v": PIP_SMALL_V, "items": {}, "legacy": False}
    items = meta["items"]

    todo = []
    for it in pool:
        src = it["path"]
        dst = mapping[src]
        key = os.path.basename(dst)
        ssz, smt = _stat_pair(src)
        dsz, dmt = _stat_pair(dst)

        rec = items.get(key)
        hit = bool(
            isinstance(rec, dict) and rec.get("src") == src and dsz > 4096
            and (ssz, smt) == (rec.get("size"), rec.get("mtime"))
            and (dsz, dmt) == (rec.get("out"), rec.get("outmtime"))
            and float(rec.get("short") or 0) >= float(short_side)
        )
        if (not hit and ver_ok and meta.get("legacy", True) and rec is None
                and dsz > 4096 and dmt >= smt and short_side <= PIP_SMALL_MIN_SHORT):
            # 老版本留下的副本（台账里完全没登记过）：认下来并补登记。
            # 注意 short 记的是下限值——我们无法得知当年按多大建的，
            # 记小了意味着"下次要求更大时会重建"，偏保守但不会出错。
            items[key] = {"src": src, "size": ssz, "mtime": smt,
                          "out": dsz, "outmtime": dmt,
                          "short": float(PIP_SMALL_MIN_SHORT)}
            hit = True
        if not hit:
            # 失败后保留失效标记，下一次必须重试，不能把旧文件认领成有效副本。
            items[key] = {"src": src, "short": 0}
            todo.append((it, dst))
    return mapping, todo, meta


def prepare_small_pool(pool, short_side, log=None, stop_event=None, threads=None,
                       progress_cb=None, hw=None):
    """同一素材目录串行核对/更新缓存，目录内的副本仍由线程池并行生成。"""
    if not pool:
        return {}
    # 多条主视频及后台预热会共用副本、临时文件和台账；必须先等前一批发布，
    # 再核对缓存，否则相同素材会重复转码，甚至因临时文件竞争回退到原片。
    folder = os.path.realpath(small_dir_of(os.path.dirname(pool[0]["path"])))
    # ponytail: 同目录冷缓存请求串行；若不重叠素材吞吐成为瓶颈，再拆逐文件锁和台账锁。
    lock = _folder_lock(folder)
    while not lock.acquire(timeout=0.1):
        if stop_event is not None and stop_event.is_set():
            return {}
    try:
        if stop_event is not None and stop_event.is_set():
            return {}
        return _prepare_small_pool(pool, short_side, log, stop_event, threads,
                                   progress_cb, hw)
    finally:
        lock.release()


def _prepare_small_pool(pool, short_side, log=None, stop_event=None, threads=None,
                        progress_cb=None, hw=None):
    """
    为素材池准备"低清副本"，返回 {原素材路径: 副本路径}。

    为什么值得做（逐项消融实测，48 段 / 3 并发，见 README 性能实测表）：
        用原素材拼接      13~14 秒，内存峰值 6.2 GB
        用低清副本拼接     1.0 秒，内存峰值 0.84 GB
    因为画中画在成片里最终只占约 260x384 像素，解码 1080p 素材纯属浪费。

    **副本是长期缓存，不会被删**：生成一次后一直留在 <素材目录>/.pip_small/ 里，
    本函数每次运行都会先按台账盘点一遍——已经生成好的直接拿来用（只 stat，不解码、
    不转码），只有缺的 / 过期了的才新建。所以第二次以后基本是秒过。

    容错：素材目录不可写时仍使用已验证的副本；仅缺失或生成失败的素材
    回退原片，绝不把失败重建留下的旧文件标成有效缓存。

    :param pool      : scan_pip_pool 的结果（元素含 path/dur/w/h）
    :param short_side: 副本短边像素（见 _small_short_side）
    :param progress_cb: 可选的进度回调 (已完成数, 总数)，用于驱动进度条而不是刷日志
    :param hw       : 硬件加速模式（见 hw_decode_args），None = 用默认
    :return: {原素材: 副本}；不适用时返回 {}
    """
    if not pool:
        return {}
    folder = os.path.dirname(pool[0]["path"])
    small_dir = small_dir_of(folder)

    # ---- 先盘点：哪些早就生成好了（这一步不转码，几百个素材几毫秒） ----
    t0 = time.time()
    mapping, todo, meta = _small_plan(pool, small_dir, short_side)
    ready = len(mapping) - len(todo)
    if progress_cb is not None:
        progress_cb(ready, len(pool))

    # ---- 全都现成：直接调用，一个字节都不重做 ----
    if not todo:
        if log:
            log(f"   低清副本：{ready}/{len(pool)} 个已生成，直接复用"
                f"（核对用时 {time.time() - t0:.2f} 秒）")
        _write_small_meta(small_dir, meta)
        return mapping

    pending = {it["path"] for it, _ in todo}
    ok = {src: dst for src, dst in mapping.items() if src not in pending}
    # ---- 只在需要生成时探测写权限；只读目录仍复用已验证的副本 ----
    try:
        os.makedirs(small_dir, exist_ok=True)
        probe = os.path.join(small_dir, ".write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
    except Exception:
        if log:
            log("   素材目录不可写，复用已有有效副本，缺失素材回退原片")
        return ok

    _ensure_small_roster(small_dir)

    if log:
        if ready:
            log(f"   低清副本：已有 {ready} 个可直接用，新建 {len(todo)} 个"
                f"（每个只做一次，之后一直复用）…")
        else:
            log(f"   首次用到这 {len(todo)} 个素材，正在建立低清副本"
                f"（只做一次，之后一直复用）…")

    items = meta["items"]
    t1 = time.time()
    done = [0]
    lock = threading.Lock()

    def one(job):
        if stop_event is not None and stop_event.is_set():
            return
        it, dst = job
        built = False
        try:
            built = _build_small_copy(it, dst, short_side, hw)
        except Exception:
            pass
        dsz, dmt = _stat_pair(dst)
        ssz, smt = _stat_pair(it["path"])
        with lock:
            if built and dsz > 4096:
                ok[it["path"]] = dst
                items[os.path.basename(dst)] = {
                    "src": it["path"], "size": ssz, "mtime": smt,
                    "out": dsz, "outmtime": dmt, "short": float(short_side)}
            done[0] += 1
            # 在锁内按顺序发送，避免并发完成时进度倒退。
            if progress_cb is not None:
                progress_cb(ready + done[0], len(pool))

    with ThreadPoolExecutor(max_workers=max(1, int(threads or PIP_SMALL_THREADS))) as ex:
        list(ex.map(one, todo))

    # ---- 台账落盘：以后就靠它判断"已生成、可直接调用" ----
    _write_small_meta(small_dir, meta)
    if log:
        log(f"   低清副本：本次新建 {len(ok) - ready}/{len(todo)} 个，"
            f"用时 {time.time() - t1:.1f} 秒，未成功的回退原片。")
    return ok


# 已在做"整池补齐"的素材目录（避免重复开线程）
_SMALL_WARMING = set()
_SMALL_WARM_LOCK = threading.Lock()


def _warm_small_pool_async(folder, main_info, opts, log=None):
    """
    后台把整池素材的加速副本补齐（不占用本次任务的等待时间）。

    为什么需要：只建"本次抽到的"素材能让当前任务立刻开跑，但下次随机抽到别的
    素材又得重新等十几秒。所以在全部视频处理完之后，用低并发把剩下的慢慢补上，
    等用户下次点开始时整池都已就绪，几乎是秒开。

    中断无害：副本是逐个原子落盘的，中途退出只是没补完，下次接着补。
    """
    with _SMALL_WARM_LOCK:
        if folder in _SMALL_WARMING:
            return
        _SMALL_WARMING.add(folder)

    def work():
        try:
            pool = scan_pip_pool(folder)
            if not pool:
                return
            short = _small_short_side(main_info["width"], main_info["height"], opts)
            if log:
                log("需要补建的副本：后台正在处理（不影响使用，下次会快很多）…")
            # 并发压到 2：这是"搭便车"的活，不能跟用户正在跑的活抢机器
            got = prepare_small_pool(pool, short, None, opts.get("stop_event"), threads=2,
                                     hw=opts.get("hwaccel"))
            if log and len(got) == len(pool):
                log("低清副本已补齐，以后处理视频会更快。")
        except Exception:
            pass
        finally:
            with _SMALL_WARM_LOCK:
                _SMALL_WARMING.discard(folder)

    threading.Thread(target=work, daemon=True).start()
