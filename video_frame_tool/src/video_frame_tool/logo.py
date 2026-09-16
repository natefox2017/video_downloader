"""程序图标：外置 logo 优先，缺失时回退内嵌 PNG（纯标准库 zlib 解码）。"""

import os
import struct
import tempfile
import tkinter as tk
import zlib

from .platform_compat import user_config_dir

# ============================================================================
# 一·七、程序图标（内嵌 logo）
# ============================================================================
# 图标直接以 base64 PNG 内嵌在源码里，**不需要任何外部图片文件**，
# 拷走单个 .py 就能在三大平台正常显示窗口图标，也不用装 Pillow 之类的库。
#
# 想换成自己的品牌 logo：把一个 png 图片命名为 logo.png，
# 放到（任选其一，优先脚本同目录）：
#     1) video_frame_tool.py 所在目录
#     2) 用户配置目录（位置见"零、平台适配层"的 user_config_dir）
# 程序启动时优先用你的图片，找不到才用内嵌图标。建议正方形、256x256 以上。
#
# ⚠ 为什么下面要自己解 PNG（实测踩过的坑，别删）：
#   macOS 自带的 /usr/bin/python3 绑的是 **Tk 8.5**，它既不认 PNG
#   （`PhotoImage(data=png_base64)` → "couldn't recognize image data"），
#   也不认 base64 形式的 PPM。Tk 8.5 唯一能读的位图格式是**PPM 文件**。
#   于是这里用纯标准库把内嵌 PNG 解出来、缩到目标尺寸、落盘成 PPM 再加载。
#   Tk 8.6+ 走正常 PNG 路径，只在 8.5 上才触发这条回退。
# ============================================================================

LOGO_FILE_NAME = "logo.png"          # 自定义 logo 的文件名
_LOGO_PPM_FILES = {}                 # size -> 已经转好的 PPM 临时文件路径
_LOGO_EMBEDDED_CACHE = []            # 内嵌 PNG 解码后的字节（延迟解，避免 import 期开销）
LOGO_PNG_B64 = (
    "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"
    "RK5CYII="
)


def _logo_embedded_png():
    """内嵌 logo 的 PNG 原始字节（解一次就缓存）。"""
    import base64 as _b64
    if not _LOGO_EMBEDDED_CACHE:
        _LOGO_EMBEDDED_CACHE.append(_b64.b64decode(LOGO_PNG_B64))
    return _LOGO_EMBEDDED_CACHE[0]


def _png_decode(png_bytes):
    """
    纯标准库解 8 位 PNG，返回 (宽, 高, 通道数, 像素字节)。

    只服务内嵌 logo：macOS 自带 python3 的 Tk 是 8.5，读不了 PNG，必须自己解。
    支持颜色类型 0/2/4/6（灰度 / RGB / 灰度+A / RGBA）与全部 5 种行滤波方式。
    调色板（类型 3）与 16 位深不支持——内嵌图是我们自己生成的，不会用到。
    """
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 数据")
    pos, idat, w, h, ct = 8, bytearray(), 0, 0, 0
    while pos + 8 <= len(png_bytes):
        (ln,) = struct.unpack(">I", png_bytes[pos:pos + 4])
        typ = png_bytes[pos + 4:pos + 8]
        body = png_bytes[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", body[:10])
            if bd != 8:
                raise ValueError("只支持 8 位 PNG")
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    ch = {0: 1, 2: 3, 4: 2, 6: 4}.get(ct)
    if not ch:
        raise ValueError("不支持的颜色类型 %s" % ct)

    raw = zlib.decompress(bytes(idat))
    stride, p = w * ch, 0
    prev = bytearray(stride)
    out = bytearray()
    for _ in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:                                   # Sub
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:                                 # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:                                 # Average
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:                                 # Paeth
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                c = prev[i - ch] if i >= ch else 0
                b = prev[i]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif f != 0:
            raise ValueError("不支持的行滤波 %d" % f)
        out += line
        prev = line
    return w, h, ch, bytes(out)


def _png_to_ppm_file(png_bytes, size):
    """
    把 PNG 缩小到 size×size 并落盘为 PPM（P6），返回文件路径。

    Tk 8.5 只认 PPM 文件，这是老 Tk 上唯一能让 logo 显示出来的路子。
    缩放用最近邻抽样（图标是几何色块，效果足够，且零依赖）。
    """
    w, h, ch, px = _png_decode(png_bytes)
    if size > w:                                     # 目标比原图大就不放大，避免无谓糊图
        size = w
    step = max(1, w // size)
    vstep = max(1, h // size)
    rows = []
    for y in range(0, h, vstep):
        row = bytearray()
        base = y * w
        for x in range(0, w, step):
            o = (base + x) * ch
            if ch >= 3:
                row += px[o:o + 3]
            else:
                row += bytes((px[o],)) * 3
        rows.append(bytes(row))
    rows = rows[:size]

    path = os.path.join(tempfile.gettempdir(), "video_frame_tool_logo_%d.ppm" % size)
    with open(path, "wb") as fh:
        fh.write(b"P6\n%d %d\n255\n" % (size, len(rows)))
        for r in rows:
            fh.write(r)
    return path


def _logo_search_paths():
    """自定义 logo 的查找位置（按优先级）

    拆分前 logo.png 与单文件脚本同目录；现在包代码在 src/video_frame_tool/ 下，
    所以同时找「包目录」和「项目根目录」，老习惯（把 logo.png 丢在项目里）继续有效。
    """
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(pkg_dir))
    return [
        os.path.join(pkg_dir, LOGO_FILE_NAME),
        os.path.join(project_root, LOGO_FILE_NAME),
        os.path.join(user_config_dir(), LOGO_FILE_NAME),
    ]


def _logo_shrink(img, size):
    """Tk 只能整数倍缩图，取最接近的倍数把 img 缩到约 size 像素。"""
    factor = max(1, int(round(max(img.width(), img.height()) / float(size))))
    return img.subsample(factor, factor) if factor > 1 else img


def _logo_load_via_ppm(png_bytes, size):
    """
    老 Tk（8.5）回退路径：PNG → PPM 临时文件 → PhotoImage。
    转好的 PPM 按尺寸缓存，避免窗口图标与标题图标各转一次。
    """
    path = _LOGO_PPM_FILES.get(size)
    if not path or not os.path.isfile(path):
        path = _png_to_ppm_file(png_bytes, size)
        _LOGO_PPM_FILES[size] = path
    return tk.PhotoImage(file=path)


def load_logo(size=64):
    """
    载入 logo 图片，返回 (PhotoImage 或 None, 来源说明)。

    优先级：脚本同目录的 logo.png → 配置目录的 logo.png → 内嵌图标。
    每一条都先试 Tk 原生 PNG 加载，失败再走 PPM 回退（Tk 8.5 场景）。
    必须在创建 Tk 根窗口之后调用（PhotoImage 依赖 Tk 环境）。
    任何失败都只返回 (None, 原因)，不抛异常、不影响主流程。
    """
    # ---- 1) 用户自定义图片 ----
    for path in _logo_search_paths():
        if not os.path.isfile(path):
            continue
        try:
            return _logo_shrink(tk.PhotoImage(file=path), size), path
        except Exception:
            pass
        try:                                          # 老 Tk 读不了 PNG 文件 → 自己转 PPM
            with open(path, "rb") as fh:
                return _logo_load_via_ppm(fh.read(), size), path + "（PPM 回退）"
        except Exception:
            continue                                  # 用户图片读不了就继续看下一个

    # ---- 2) 内嵌图标：Tk 8.6+ 直接吃 base64 PNG ----
    try:
        return _logo_shrink(tk.PhotoImage(data=LOGO_PNG_B64), size), "内嵌图标"
    except Exception:
        pass

    # ---- 3) 内嵌图标：PPM 回退（Tk 8.5）----
    try:
        return _logo_load_via_ppm(_logo_embedded_png(), size), "内嵌图标（PPM 回退）"
    except Exception as e:
        return None, "内嵌图标不可用（%s）" % e
