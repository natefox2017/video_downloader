"""回归检查：前贴/尾贴随机抽取、命名、拼接规格、封面替换、复刻22 混淆产物特征。

只用标准库与已安装的 ffmpeg，所有素材仅写入临时目录，不碰用户素材。

跑法：python3 tests/test_video_frame_tool.py
"""
import os
import subprocess
import struct
import sys
import tempfile
import threading
from pathlib import Path

# 直接跑 `python3 tests/test_video_frame_tool.py` 时把项目根加进 sys.path（src 包在项目根下）；
# 已经 `pip install -e .` 的环境不受影响。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src as tool
from src import fission
from src import obfuscate as core_obfuscate

W, H = 720, 1276          # 复刻22 的成品规格（对齐样本 22.mp4）


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------

def ff(args, check=True):
    """跑一条 ffmpeg 命令并返回 stdout。"""
    return subprocess.run([tool.FFMPEG, "-v", "error", "-y"] + args,
                          check=check, capture_output=True).stdout


def make_clip(path, color, seconds, rate=25, audio=False, size="320x240"):
    """造一段纯色测试素材（可选带 1kHz 正弦音轨）。"""
    args = ["-f", "lavfi", "-i", f"color={color}:size={size}:rate={rate}"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100"]
    args += ["-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
    else:
        args += ["-an"]
    ff(args + [str(path)])


def gray_frames(path, indices, w=W, h=H):
    """按帧号解码成灰度帧（每帧 w*h 字节）。"""
    expr = "+".join("eq(n\\,%d)" % i for i in indices)
    raw = subprocess.run([tool.FFMPEG, "-v", "error", "-i", str(path),
                          "-vf", "select='%s'" % expr, "-vsync", "0",
                          "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    sz = w * h
    return [raw[k * sz:(k + 1) * sz] for k in range(len(raw) // sz)]


def field_stats(frame, parity, w=W, h=H):
    """取一场（parity=0 偶行 / 1 奇行）的 (均值, 标准差)。"""
    rows = b"".join(frame[r * w:(r + 1) * w] for r in range(parity, h, 2))
    m = sum(rows) / len(rows)
    sd = (sum((v - m) ** 2 for v in rows) / len(rows)) ** 0.5
    return m, sd


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------

def check_naming_and_pick():
    """命名 = 搬运名 + 两位序号；前贴/尾贴随机抽取：够挑无放回、不够有放回。"""
    assert fission.output_name('foo', 1) == 'foo_01.mp4'
    assert fission.output_name('bar', 12) == 'bar_12.mp4'
    pool = ['a', 'b', 'c']
    assert len(fission.pick_segments(pool, 2)) == 2
    assert len(set(fission.pick_segments(pool, 2))) == 2, '够挑时不应重复'
    assert len(fission.pick_segments(pool, 5)) == 5, '不够时有放回凑满'
    assert fission.pick_segments([], 3) == [] and fission.pick_segments(pool, 0) == []
    assert fission.pick_cover([]) is None
    assert fission.pick_cover(['x']) == 'x'
    assert tool.fmt_duration(9) == '9 秒'
    assert tool.fmt_duration(65) == '1 分 5 秒'
    assert tool.fmt_duration(3700) == '1 小时 1 分'
    print('命名与随机抽取检查通过')


def check_concat_segments():
    """前贴 + 搬运 + 尾贴：拼成固定 720x1276 / 30fps，时长 = 各段之和。"""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        head, main, tail = root / 'h.mp4', root / 'm.mp4', root / 't.mp4'
        make_clip(head, 'green', 1)
        make_clip(main, 'red', 2)
        make_clip(tail, 'blue', 1)
        out = root / 'joined.mp4'
        segments = [(str(p), False, d) for p, d in
                    ((head, 1), (main, 2), (tail, 1))]
        fission.concat_segments(segments, str(out), tool.FFMPEG,
                                tool.ProcRegistry(), threading.Event(), threads=1)
        data = tool._probe_json(str(out))
        v = next(s for s in data['streams'] if s['codec_type'] == 'video')
        assert (v['width'], v['height']) == (W, H), (v['width'], v['height'])
        assert abs(float(v['duration']) - 4) < 0.2, v['duration']   # 1+2+1
    print('前贴+搬运+尾贴拼接：固定 720x1276、时长正确检查通过')


def check_cover():
    """封面：图片强制拉伸替换第 0 帧；时长/帧数不变，第 1 帧起不受影响。"""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        main, cover = root / 'm.mp4', root / 'c.png'
        make_clip(main, 'red', 2)
        ff(['-f', 'lavfi', '-i', 'color=yellow:size=200x400', '-frames:v', '1',
            str(cover)])
        seg = [(str(main), False, 2.0)]

        base = root / 'base.mp4'
        fission.concat_segments(seg, str(base), tool.FFMPEG, tool.ProcRegistry(),
                                threading.Event(), threads=1)
        with_cov = root / 'cov.mp4'
        fission.concat_segments(seg, str(with_cov), tool.FFMPEG, tool.ProcRegistry(),
                                threading.Event(), threads=1, cover=str(cover))

        data = tool._probe_json(str(with_cov))
        v = next(s for s in data['streams'] if s['codec_type'] == 'video')
        assert (v['width'], v['height']) == (W, H), (v['width'], v['height'])
        assert abs(float(v['duration']) - 2) < 0.05, v['duration']   # 封面不占时长

        f0_cov, f0_base = root / 'f0c.png', root / 'f0b.png'
        f1_cov, f1_base = root / 'f1c.png', root / 'f1b.png'
        ff(['-i', str(with_cov), '-frames:v', '1', str(f0_cov)])
        ff(['-i', str(base), '-frames:v', '1', str(f0_base)])
        ff(['-i', str(with_cov), '-vf', 'select=eq(n\\,1)', '-frames:v', '1', str(f1_cov)])
        ff(['-i', str(base), '-vf', 'select=eq(n\\,1)', '-frames:v', '1', str(f1_base)])
        assert f0_cov.read_bytes() != f0_base.read_bytes(), '第 0 帧应已被封面替换'
        assert f1_cov.read_bytes() == f1_base.read_bytes(), '第 1 帧及之后不应被改动'
    print('封面：第 0 帧替换、时长/帧数不变检查通过')


def check_fieldmix_recipe():
    """复刻22 的配方：滤镜链、场序、截帧、容器伪装值都必须与样本一致。"""
    cmd = core_obfuscate._encode_cmd_fieldmix(tool.FFMPEG, 'in.mp4', 'out.mkv',
                                              frames=929)
    filters = cmd[cmd.index('-filter_complex') + 1]
    # 色块图自带「偶行不透明」的 alpha 遮罩，overlay 一次覆盖偶行、奇行保留原画
    assert "a='if(eq(mod(Y,2),0),255,0)'" in filters, filters
    assert "overlay=format=yuv444" in filters, filters
    # 第 0 帧两场都用原画（否则封面就是彩虹横纹）：
    # overlay 的 enable 用从 0 起算的 n，写 gte(n,1) 才是「第 0 帧不动」
    assert "enable='gte(n,1)'" in filters, filters
    # 逐像素判行奇偶的旧写法必须彻底消失（它是编码耗时的大头，别再退回去）
    assert "blend=all_expr" not in filters, filters
    assert "loop=loop=-1" in filters, '色块图必须循环复用'
    assert "setfield=bff" in filters and "interl=1" in filters, filters
    assert cmd[cmd.index('-top') + 1] == '0', '画面写 BFF'
    assert cmd[cmd.index('-frames:v') + 1] == '929', '须按源帧数 -1 截帧'

    # 容器：Duration 写死 1032ms、DURATION 写死 1.121s（与样本一致）
    assert core_obfuscate.FIELD_DURATION_MS == 1032.0
    assert core_obfuscate.FIELD_DUR_TEXT == b'00:00:01.121000000\x00'
    assert core_obfuscate.DEFAULT_ALGORITHM == 'fieldmix'
    assert set(core_obfuscate.ALGORITHMS) == {'fieldmix'}
    assert core_obfuscate.ALGORITHMS['fieldmix']['needs_frames'] is True
    assert core_obfuscate.get_algorithm('不存在') is \
        core_obfuscate.ALGORITHMS['fieldmix']

    with tempfile.TemporaryDirectory() as folder:
        mid = Path(folder) / 'mid.mkv'
        ff(['-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30', '-t', '1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an',
            '-f', 'matroska', str(mid)])
        blob = core_obfuscate._clone_container(mid.read_bytes())
        tree = core_obfuscate._parse(blob, 0, len(blob))
        seg = core_obfuscate._find(tree, 0x18538067)
        info = core_obfuscate._find(seg, 0x1549A966)
        assert core_obfuscate._find(info, 0x4489).data == struct.pack('>d', 1032.0)
        assert b'00:00:01.121000000\x00' in blob
        # 同一份中间件改写两次，DateUTC 不同 → 字节必不同（裂变哈希唯一的依据）
        core_now = core_obfuscate._now_date_ns
        try:
            core_obfuscate._now_date_ns = lambda: 111
            a = core_obfuscate._clone_container(mid.read_bytes())
            core_obfuscate._now_date_ns = lambda: 222
            b = core_obfuscate._clone_container(mid.read_bytes())
        finally:
            core_obfuscate._now_date_ns = core_now
        assert a != b, 'DateUTC 变化时容器字节必须随之变化'
    print('复刻22 配方：滤镜链、场序、截帧、容器伪装值检查通过')


def check_fieldmix_end_to_end():
    """端到端一条成品：画面场结构对、容器拒读、两条产物哈希不同。"""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        m1, m2 = root / 'a.mp4', root / 'b.mp4'
        make_clip(m1, 'red', 2.0, size='720x1280')
        make_clip(m2, 'blue', 2.0, size='720x1280')

        # ---- 先单独验证画面配方（中间件可被 ffmpeg 正常读，便于查场结构）----
        mid = root / 'mid.mkv'
        tool._run(core_obfuscate._encode_cmd_fieldmix(tool.FFMPEG, str(m1), str(mid),
                                                      frames=59),
                  tool.ProcRegistry())
        f0, f1 = gray_frames(str(mid), [0, 1])
        e0, o0 = field_stats(f0, 0), field_stats(f0, 1)
        assert abs(e0[0] - o0[0]) < 1.0, ('第 0 帧两场应同为原画（封面干净）', e0, o0)
        e1, o1 = field_stats(f1, 0), field_stats(f1, 1)
        # 第 1 帧起：偶行是静态彩色色块（标准差 ~16），奇行是原画
        assert 10 < e1[1] < 26, ('第 1 帧偶行应是被模糊的彩色色块', e1)
        assert abs(e1[0] - o1[0]) > 3, ('第 1 帧偶行/奇行内容应不同', e1, o1)

        # ---- 再走完整流程：容器拒读 + 哈希唯一 ----
        out = root / 'out'
        out.mkdir()
        outs = fission.process_batch([str(m1), str(m2)], str(out), tool.FFMPEG,
                                     tool.ProcRegistry(), threading.Event(),
                                     threads=1)
        assert len(outs) == 2 and all(os.path.exists(p) for p in outs), outs
        for p in outs:
            r = subprocess.run([tool.FFPROBE, '-v', 'error', str(p)],
                               capture_output=True, text=True)
            assert r.returncode != 0 or 'Invalid' in r.stderr, \
                ('混淆产物应被 ffprobe 拒读', p, r.stderr)
        assert Path(outs[0]).read_bytes() != Path(outs[1]).read_bytes(), \
            '两条产物哈希必须不同'
    print('复刻22 端到端：场结构、容器拒读、哈希唯一检查通过')


def check_probe_and_run():
    """基础：probe_media / probe_frames 解析、_run 对坏输入抛错。"""
    with tempfile.TemporaryDirectory() as folder:
        src = Path(folder) / 'm.mp4'
        make_clip(src, 'red', 1.0)          # 25fps 1 秒 → 25 帧
        info = tool.probe_media(str(src))
        assert (info['width'], info['height']) == (320, 240), info
        assert info['fps'] == 25.0 and not info['has_audio'], info
        assert 0 < info['duration'] <= 1.2, info['duration']
        n = tool.probe_frames(str(src))
        assert n == 25, n
        assert tool.probe_frames('/nonexistent.mp4') == 0
    try:
        tool._run([tool.FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
                   '-nostats', '-progress', 'pipe:1',
                   '-i', '/nonexistent-input.mp4', os.devnull], tool.ProcRegistry())
        raise AssertionError('坏输入必须抛错')
    except RuntimeError as e:
        assert 'nonexistent' in str(e), e
    print('probe_media / probe_frames 与 _run 错误处理检查通过')


if __name__ == '__main__':
    check_naming_and_pick()
    check_concat_segments()
    check_cover()
    check_fieldmix_recipe()
    check_fieldmix_end_to_end()
    check_probe_and_run()
    print('全部回归检查通过')
