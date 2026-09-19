"""容器「混淆 / 复刻」（界面主流程的最后一步）。

只做一种效果：**复刻参考样本 22.mp4**（原去重软件的输出），靠「一次 x264 重编码 +
一次纯 Python 容器改写」实现本地拒读、平台可播。

  fieldmix（唯一算法，2026-09-19 逐场逆向 22.mp4 得出）
      画面：偶行场铺一张静态强彩色色块图、奇行场放原画面，**第 0 帧整帧保留原画**
            （平台用第一帧取封面，第 0 帧若是色块，成品封面就是彩虹横纹）；
            色块图自带「偶行不透明」的 alpha 遮罩，靠 overlay 一次覆盖完成，
            不逐像素判行奇偶（合成开销降到原来的 ~1/4，输出逐帧一致）；
            写 BFF 场序 + 真隔行编码，容器 FieldOrder 仍伪装成 TFF。
      容器：Duration 写死 1032ms、Tags DURATION 写死 "00:00:01.121"，与样本一致。

原理、配方与逐场量化数据见 `docs/技术原理.md`（本项目唯一的原理文档）。

只依赖标准库 + ffmpeg；EBML 读写是自写的极简实现（见 _read_vint / _parse）。
容器形状、CRC 落盘约定、写死的假元数据都与参考样本保持一致，
改动前先看 _clone_container 里的注释。"""

import os
import shutil
import struct
import tempfile
import time
import zlib

from .probe import probe_frames
from .proc import _check_stopped, _run

# ============================================================================
# 一、EBML / Matroska 基础（自写极简实现，只覆盖本模块用到的元素）
# ============================================================================

# 需要递归解析的「容器型」元素 ID
_MASTER_IDS = frozenset([
    0x1A45DFA3, 0x18538067, 0x114D9B74, 0x4DBB, 0x1549A966, 0x1654AE6B,
    0xAE, 0xE0, 0xE1, 0x1F43B675, 0xA0, 0x1254C367, 0x7373, 0x63C0, 0x67C8,
    0x1C53BB6B, 0xBB, 0xB7, 0x55B0, 0x1043A770,
])


def _read_vint(buf, pos, keep_marker=False):
    """读一个 EBML 变长整数，返回 (值, 占用字节数)。"""
    first = buf[pos]
    if first == 0:
        return None, 1
    mask, length = 0x80, 1
    while not (first & mask):
        mask >>= 1
        length += 1
        if length > 8:
            return None, 0
    val = first if keep_marker else (first & (mask - 1))
    for k in range(1, length):
        val = (val << 8) | buf[pos + k]
    return val, length


def _write_id(eid):
    n = 1
    while eid >= (1 << (8 * n)):
        n += 1
    return eid.to_bytes(n, "big")


def _write_size(val):
    """按最短长度写 EBML size（与 ffmpeg 的写法一致）。"""
    n = 1
    while val >= (1 << (7 * n)) - 1:
        n += 1
        if n > 8:
            raise ValueError("size 过大，无法编码")
    out = bytearray(val.to_bytes(n, "big"))
    out[0] |= 0x80 >> (n - 1)
    return bytes(out)


class _Node(object):
    """一个 EBML 元素：children 非 None 即容器元素，否则 data 是载荷字节。"""

    __slots__ = ("eid", "data", "children")

    def __init__(self, eid, data=None, children=None):
        self.eid = eid
        self.data = data
        self.children = children

    @property
    def is_master(self):
        return self.children is not None


def _parse(buf, start, end):
    """把一个容器区间解析成 _Node 列表；结构不认识就停在那（不抛异常）。"""
    nodes = []
    i = start
    while i < end:
        try:
            eid, il = _read_vint(buf, i, True)
            size, sl = _read_vint(buf, i + il, False)
        except Exception:
            break
        if eid is None or size is None or sl == 0:
            break
        head = i + il + sl
        unknown = size == (1 << (7 * sl)) - 1
        stop = end if unknown else head + size
        if stop > end:
            break
        if eid in _MASTER_IDS:
            nodes.append(_Node(eid, children=_parse(buf, head, stop)))
        else:
            nodes.append(_Node(eid, data=bytes(buf[head:stop])))
        i = stop
    return nodes


def _serialize(nodes):
    """把 _Node 列表写回字节（容器元素递归展开）。"""
    chunks = []
    for nd in nodes:
        body = _serialize(nd.children) if nd.is_master else (nd.data or b"")
        chunks.append(_write_id(nd.eid) + _write_size(len(body)) + body)
    return b"".join(chunks)


def _find(nodes, eid):
    if isinstance(nodes, _Node):
        nodes = nodes.children or []
    for nd in nodes:
        if nd.eid == eid:
            return nd
    return None


def _find_all(nodes, eid):
    return [nd for nd in nodes if nd.eid == eid]


# ============================================================================
# 二、x264 编码配方（从样本 22.mp4 的 SEI 参数表逐项抄回，别凭直觉改）
# ============================================================================
#
#   deblock            x264 的解析是 "enable:alpha:beta"，而 ffmpeg 用 ':' 分隔
#                      -x264-params，所以要写成 deblock=1\:0\:0（反斜杠转义），
#                      码流里才会渲染成样本的 deblock=1:1:0。
#   partitions         用 x264 名字列表写法，才会渲染成样本的 analyse=0x3:0x113。
#   chroma-qp-offset   x264 在 subme>=7 时会再减 2，所以这里填 -2，
#                      最终码流里才是样本的 chroma_qp_offset=-4。
#   mbtree             preset superfast 自带 --no-mbtree，样本是 mbtree=1，要显式打开。
#   隔行               样本是 field_order=tt，靠 ffmpeg 的 -flags +ilme+ildct -top 1
#                      打开；必须放在 -x264-params 之前才生效。
CLONE_X264 = [
    "ref=3", "deblock=1\\:0\\:0", "partitions=i4x4,i8x8,p8x8,b8x8", "me=hex",
    "subme=7", "psy-rd=1.00,0.00", "mixed-refs=0", "me-range=16", "trellis=0",
    "chroma-qp-offset=-2", "threads=4", "lookahead-threads=4",
    "sliced-threads=1", "slices=4", "bframes=3", "b-pyramid=2", "b-adapt=1",
    "b-bias=0", "direct=1", "weightb=1", "open-gop=0", "weightp=0",
    "keyint=250", "keyint-min=25", "scenecut=40", "intra-refresh=0",
    "rc-lookahead=10", "qcomp=0.60", "aq-mode=1", "aq-strength=1.0", "mbtree=1",
]

# 对外规格与样本 22.mp4 一致（用户明确要求：分辨率/帧率不要按原视频，按样本）
CLONE_W, CLONE_H, CLONE_FPS, CLONE_CRF = 720, 1276, "30", "22"

# 重编码耗时占绝对大头（实测容器改写不到 1 秒），进度权重按这个比例折算
_ENCODE_WEIGHT = 0.95


def _now_date_ns():
    """当前时间的 macOS 日期表示（2001-01-01 起的纳秒）。

    写进容器的 DateUTC：每份成品的处理时刻都不同 → 同一素材裂变多份必然哈希互异。
    """
    return int((time.time() - 978307200) * 1e9)


# ---- fieldmix：复刻 22.mp4 的上下场混合 ------------------------------------------
# 样本实测量化（见 docs/技术原理.md 第五节）：
#   偶行场 = 一整张静态强彩色色块图，全程不变（均值 163.5 / 标准差 16.5，跨 920 帧 MAD ≤ 0.49）
#   奇行场 = 真实画面 = 原片对应帧（与 11.mp4 同场 MAD ≈ 10，重编码噪声级）
#   11[i] ↔ 22[i] 无时移；帧数 11(931) → 22(930)，即样本丢掉了最后一帧
FIELD_DURATION_MS = 1032.0                        # 容器 Duration 写死值（样本如此）
FIELD_DUR_TEXT = b"00:00:01.121000000\x00"        # Tags DURATION 写死值（样本如此）

# 第 0 帧整帧保留原画：平台用第一帧取封面，不能让它看到色块。
# overlay 的 enable 用的是**从 0 起算**的帧号 n（与 blend 的 N 从 1 起算不同），
# 所以这里直接写 n>=1。曾用 blend + 逐像素表达式，2026-09-19 为了提速改成
# 「alpha 行遮罩 + overlay」（见 _fieldmix_pattern 的注释），实测同一素材逐帧 bit-exact。
_OVERLAY_EXPR = "gte(n,1)"
# 色块图的行遮罩：偶行完全不透明（盖住原画），奇行全透明（保留原画）。
_ALPHA_MASK = "if(eq(mod(Y,2),0),255,0)"


def _fieldmix_pattern(seed):
    """生成一整张静态强彩色色块图，并把「偶行可见」写进 alpha 通道（每次调用重新随机）。

    只出 1 帧再 loop 复用：避免逐帧重算导致闪烁与码率暴涨。

    提速要点（2026-09-19）：原先用 `blend=all_expr='if(eq(mod(Y,2),0),...)'` 逐像素
    求值判行奇偶，实测占单条编码 CPU 的 ~70%（20s 片段 55.6s CPU → 16.2s）。
    现在改成「色块图自带 alpha 行遮罩 + overlay」，行奇偶判断退化成一次性的
    1 帧遮罩生成，合成时只做 SIMD 覆盖，**输出逐帧 bit-exact 不变**。
    """
    return (
        "nullsrc=size=%sx%s:rate=%s,trim=end_frame=1,format=gbrp,"
        "geq=r='160+50*sin(floor(X/90)*12.9898+floor(Y/90)*78.233+%d)':"
        "g='167+50*sin(floor(X/90)*39.3468+floor(Y/90)*11.135+%d)':"
        "b='155+50*sin(floor(X/90)*73.156+floor(Y/90)*52.235+%d)',"
        "boxblur=30:2,format=yuv444p,format=yuva444p,"
        # geq 在 yuv 系要写 lum/cb/cr 名字（写 y/u/v 会报 Option not found）；
        # p(X,Y) 取原像素，只把 alpha 换成行遮罩。
        "geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':a='%s',"
        "loop=loop=-1:size=1:start=0,setpts=N/(%s*TB)[pat]"
    ) % (CLONE_W, CLONE_H, CLONE_FPS, seed, seed + 1, seed + 2,
         _ALPHA_MASK, CLONE_FPS)


def _encode_cmd_fieldmix(ffmpeg, src, mid, frames=None):
    """构造 fieldmix 的重编码命令（先出标准 MKV，容器改写是后面一步）。

    frames: 限制输出帧数。调用方按「源帧数 - 1」传入 —— 复刻样本丢掉最后一帧的行为。
    """
    seed = time.time_ns() % 1000000
    filters = (
        "[0:v]scale=%s:%s,fps=%s,format=yuv444p,lutyuv=y='val-10'[src];"
        % (CLONE_W, CLONE_H, CLONE_FPS)
        + _fieldmix_pattern(seed) + ";"
        # 第 0 帧两场都用原画：平台用第一帧取封面，不能让它看到色块。
        # format=yuv444 必须显式写：默认 auto 会走 RGB 混合，YUV<->RGB 往返会有 ±1 级误差。
        "[src][pat]overlay=format=yuv444:enable='%s':shortest=1," % _OVERLAY_EXPR
        # 转 4:2:0 时必须按隔行场分别采样色度，否则强彩色上场会污染原画下场。
        + "setfield=bff,scale=%s:%s:interl=1,format=yuv420p[v]"
        % (CLONE_W, CLONE_H)
    )
    return [
        ffmpeg, "-y", "-hide_banner", "-nostats", "-v", "error",
        "-i", src,
        "-filter_complex", filters, "-map", "[v]", "-map", "0:a:0?",
        # 画面内部写 BFF（与样本一致），容器那边的 FieldOrder 仍伪装成 TFF。
        "-flags", "+ilme+ildct", "-top", "0",
        "-c:v", "libx264", "-preset", "superfast", "-crf", CLONE_CRF,
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-x264-params", ":".join(CLONE_X264),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        *(["-frames:v", str(int(frames))] if frames else []),
        "-progress", "pipe:1",
        "-f", "matroska", mid,
    ]


# ============================================================================
# 三、容器改写：把 ffmpeg 产出的标准 MKV 改成样本 22.mp4 的形状
# ============================================================================

def _uint_bytes(v):
    """无符号整数的最短大端表示（EBML 整数元素都这么写）。"""
    if v <= 0:
        return b"\x00"
    return v.to_bytes((v.bit_length() + 7) // 8, "big")


def _crc_node(body):
    """CRC-32 元素。ffmpeg 的写法是把 zlib 结果按小端落盘（实测验证过）。"""
    return _Node(0xBF, data=struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF))


def _with_crc(children):
    """给容器补上首位的 CRC-32（校验范围 = 它后面所有子元素的字节）。"""
    return [_crc_node(_serialize(children))] + children


def _seek(seek_id, position):
    return _Node(0x4DBB, children=[
        _Node(0x53AB, data=seek_id),
        _Node(0x53AC, data=_uint_bytes(position)),
    ])


def _clone_info(duration_ms, date_ns):
    """复刻样本的 Info：没有 TimestampScale、App 名缩成 'Lavf' 且 ID 被写错 +1。"""
    kids = [
        _Node(0xEC, data=b"\x00" * 5),                       # 顶掉 TimestampScale 的位置
        _Node(0x4D81, data=b"Lavf"),                         # MuxingApp 的 ID +1
        _Node(0x5742, data=b"Lavf"),                         # WritingApp 的 ID +1
        _Node(0x4461, data=date_ns.to_bytes(8, "big", signed=True)),
        _Node(0x4489, data=struct.pack(">d", duration_ms)),
    ]
    return _Node(0x1549A966, children=_with_crc(kids))


def _clone_tracks(tracks_old):
    """
    复刻样本的 Tracks：TrackUID=1/2、补 FlagDefault=0，并把 Video 的分辨率
    元数据抹成两个 Void（★「本地打不开」就靠这一步）。
    """
    entries = _find_all(tracks_old.children, 0xAE)
    for idx, te in enumerate(entries, start=1):
        for c in te.children:
            if c.eid == 0x73C5:
                c.data = idx.to_bytes(8, "big")
        if _find(te, 0x88) is None:
            at = [k for k, c in enumerate(te.children) if c.eid == 0x22B59C]
            te.children.insert(at[0] + 1 if at else 0, _Node(0x88, data=b"\x00"))
        vid = _find(te, 0xE0)
        if vid is not None:
            # 两个 Void 顶掉 PixelWidth/PixelHeight（各 2 字节），载荷填 0（与样本一致），
            # 既保留"等长顶掉"的样本形状，又不引入会破坏容器签名的随机字节。
            vid.children = [
                _Node(0xEC, data=b"\x00\x00"),
                _Node(0xEC, data=b"\x00\x00"),
                _Node(0x54B2, data=b"\x04"),
            ]
    return _Node(0x1654AE6B, children=_with_crc(entries))


def _clone_tags(dur_text):
    """复刻样本的 Tags：两个 Tag，各挂一条 DURATION。"""
    def one(uid):
        return _Node(0x7373, children=[
            _Node(0x63C0, children=[_Node(0x63C5, data=uid.to_bytes(8, "big"))]),
            _Node(0x67C8, children=[
                _Node(0x45A3, data=b"DURATION"),
                _Node(0x4487, data=dur_text),
            ]),
        ])
    return _Node(0x1254C367, children=_with_crc([one(1), one(2)]))


def _clone_container(mid):
    """把 ffmpeg 产出的标准 MKV 改写成样本 22.mp4 的容器形状，返回新文件字节。

    Duration / DURATION 写死成样本值（FIELD_*）；DateUTC 用当前时间 →
    每份成品字节都不同。
    """
    tree = _parse(mid, 0, len(mid))
    seg = _find(tree, 0x18538067)
    if seg is None:
        raise RuntimeError("重编码产物不是合法 MKV")

    off = 0
    old_pos = []
    for n in seg.children:
        old_pos.append(off)
        off += len(_serialize([n]))
    old_first_cluster = next((o for n, o in zip(seg.children, old_pos)
                              if n.eid == 0x1F43B675), None)

    void82 = _find(seg, 0xEC)
    clusters = _find_all(seg.children, 0x1F43B675)
    cues_old = _find(seg, 0x1C53BB6B)
    if void82 is None or not clusters:
        raise RuntimeError("容器结构不符合预期")

    info = _clone_info(FIELD_DURATION_MS, _now_date_ns())
    tracks = _clone_tracks(_find(seg, 0x1654AE6B))
    tags = _clone_tags(FIELD_DUR_TEXT)

    cues = None
    if cues_old is not None:
        cues = _Node(0x1C53BB6B, children=cues_old.children)

    tail = [void82, info, tracks, tags] + clusters + ([cues] if cues else [])
    sizes = [len(_serialize([n])) for n in tail]

    def layout(seek_len):
        pos, at = [], seek_len
        for s in sizes:
            pos.append(at)
            at += s
        return pos, at

    def build_seek(pos):
        kids = [_Node(0xEC, data=b"\x00" * 4)]                   # 顶掉 CRC-32 的位置
        got = [pos[1], pos[2], pos[3], pos[-1] if cues else pos[2]]
        ids = [b"\x15\x49\xa9\x66", b"\x84\xec\xec\xec",         # 2 号是样本里被写坏的 SeekID
               b"\x12\x54\xc3\x67", b"\x1c\x53\xbb\x6b"]
        for i, sid in zip(got, ids):
            kids.append(_seek(sid, i))
        return _Node(0x114D9B74, children=kids)

    seek_len, seek = 65, None
    for _ in range(10):
        pos, _ = layout(seek_len)
        seek = build_seek(pos)
        n = len(_serialize([seek]))
        if n == seek_len:
            break
        seek_len = n
    pos, _ = layout(seek_len)
    seek = build_seek(pos)

    # Cues 的 Cluster 位置是「相对 Segment 数据起点」的绝对偏移，头部长度一变要整体平移
    if cues is not None and old_first_cluster is not None:
        delta = pos[len(tail) - len(clusters) - (1 if cues else 0)] - old_first_cluster
        for cp in _find_all(cues.children, 0xBB):
            for ctp in _find_all(cp.children, 0xB7):
                cpos = _find(ctp, 0xF1)
                if cpos is not None:
                    cpos.data = _uint_bytes(int.from_bytes(cpos.data, "big") + delta)
        k = [i for i, c in enumerate(cues.children) if c.eid == 0xBF]
        if k:
            cues.children = _with_crc(cues.children[k[0] + 1:])

    body = _serialize([seek]) + b"".join(_serialize([n]) for n in tail)
    ebml = _serialize([_find(tree, 0x1A45DFA3)])
    # Segment 的 size 固定写 8 字节（样本就是这么写的；最短编码会写成 4~5 字节）
    return ebml + b"\x18\x53\x80\x67" + b"\x01" + len(body).to_bytes(7, "big") + body


# ============================================================================
# 四、算法注册表：统一接口，可扩展
# ============================================================================
#
# 每个混淆算法是一个对象，暴露四个钩子（输入/输出约定一致）：
#   name           算法标识（唯一，界面下拉用）
#   label          展示名
#   encode_cmd(ffmpeg, src, mid, frames=None) -> list  重编码命令
#   rewrite(mid_bytes) -> bytes           把中间件改写成混淆容器
#   needs_frames   为真时调用方会按「源帧数 - 1」截帧（复刻样本丢最后一帧的行为）
#
# 加新算法 = 新增一个算法对象 + 在 ALGORITHMS 里注册一行，**不动已有算法**，
# 崩溃面被隔离。算法按「通过查重实测的日期」命名（如 260919），旧算法保留不删。
# 当前只有 fieldmix（复刻 22.mp4 的上下场混合）。
# ============================================================================


def _algo_fieldmix():
    """算法 fieldmix（2026-09-19 逐场逆向 22.mp4 得出，界面唯一算法）。

    画面：偶行场铺静态强彩色色块图、奇行场放原画面，第 0 帧整帧保留原画；
          写 BFF 场序 + 真隔行编码，容器 FieldOrder 伪装成 TFF。
    容器：Duration 写死 1032ms、Tags DURATION 写死 "00:00:01.121"。
    """
    return {
        "name": "fieldmix",
        "label": "复刻22",
        "encode_cmd": _encode_cmd_fieldmix,
        "rewrite": _clone_container,
        "needs_frames": True,
    }


# 算法注册表：name -> 算法对象。界面/调用方据此枚举和切换。
ALGORITHMS = {
    a["name"]: a for a in [
        _algo_fieldmix(),
    ]
}

DEFAULT_ALGORITHM = "fieldmix"


def get_algorithm(name=None):
    """按名字取算法对象；None 或未知名字回落到默认算法。"""
    if name is None or name not in ALGORITHMS:
        return ALGORITHMS[DEFAULT_ALGORITHM]
    return ALGORITHMS[name]


# ============================================================================
# 五、单条视频处理（给界面线程调用）
# ============================================================================

def process_video(src, out_dir, ffmpeg, registry, stop_event,
                  duration=0.0, on_progress=None, log=None, tag="", out_name=None,
                  algorithm=None):
    """
    处理单个视频：一次重编码 → 容器改写成混淆形状 → 输出到 out_dir，返回输出路径。

    :param duration: 源视频时长（秒），用于折算真实进度；读不到给 0
    :param on_progress: 真实完成度回调（0~1）
    :param log / tag: 兼容保留（本条不写日志）。界面日志统一由上层 fission 报「第几条」，
        重编码、改写容器这些内部步骤不再往日志刷屏（用户 2026-09-19 要求）。
    :param out_name: 输出文件名（不含目录）；None 时用源视频名 + .mp4
    :param algorithm: 混淆算法名（见 ALGORITHMS）；None 用默认算法
    """
    algo = get_algorithm(algorithm)
    _check_stopped({"stop_event": stop_event})

    base = os.path.splitext(os.path.basename(src))[0]
    dst = os.path.join(out_dir, out_name if out_name else base + ".mp4")
    # 输出目录选成源目录时避免同名覆盖：本流程是 Python 直接写文件，不拦会静默毁掉源文件
    if os.path.abspath(dst) == os.path.abspath(src):
        dst = os.path.join(out_dir, base + "_已处理.mp4")

    tmpdir = tempfile.mkdtemp(prefix=".ob_")
    mid = os.path.join(tmpdir, "mid.mkv")
    try:
        _check_stopped({"stop_event": stop_event})
        # fieldmix 要复刻样本「丢掉最后一帧」的行为，所以先数帧再截
        frames = None
        if algo.get("needs_frames"):
            total = probe_frames(src)
            if total > 0:
                frames = max(1, total - 1)
        cmd = algo["encode_cmd"](ffmpeg, src, mid, frames=frames)

        def report(frac):
            if on_progress:
                on_progress(min(1.0, frac * _ENCODE_WEIGHT))

        _run(cmd, registry, on_progress=report, expected_dur=float(duration or 0.0))
        _check_stopped({"stop_event": stop_event})

        with open(mid, "rb") as fh:
            out = algo["rewrite"](fh.read())
        with open(dst, "wb") as fh:
            fh.write(out)
        if on_progress:
            on_progress(1.0)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return dst
