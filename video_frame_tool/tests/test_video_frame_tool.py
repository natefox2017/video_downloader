"""回归检查：片头/片尾随机抽取、命名、混淆裂变的哈希唯一性与可读性。

只用标准库与已安装的 ffmpeg，所有素材仅写入临时目录，不碰用户素材。
"""
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# 直接跑 `python3 tests/test_video_frame_tool.py` 时把项目根加进 sys.path（src 包在项目根下）；
# 已经 `pip install -e .` 的环境不受影响。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src as tool
from src import fission


def check_fission():
    """片头/片尾随机抽取、命名、混淆裂变的哈希唯一性与可读性。"""
    # ---- 命名：主体名 + 两位序号 ----
    assert fission.output_name('foo', 1) == 'foo_01.mp4'
    assert fission.output_name('bar', 12) == 'bar_12.mp4'
    # ---- 随机抽取：够挑无放回，不够有放回 ----
    pool = ['a', 'b', 'c']
    assert len(fission.pick_segments(pool, 2)) == 2
    assert len(fission.pick_segments(pool, 5)) == 5      # 不够时有放回
    assert fission.pick_segments([], 3) == [] and fission.pick_segments(pool, 0) == []
    # ---- 混淆裂变：同一个视频裂变 2 份，产物哈希必不同、且 ffprobe 拒读 ----
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        src = root / 'main.mp4'
        subprocess.run([tool.FFMPEG, '-v', 'error', '-y', '-f', 'lavfi',
                        '-i', 'color=red:size=320x240:rate=25', '-t', '1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-an', str(src)], check=True)
        out = root / 'out'
        out.mkdir()
        outs = fission.process_one_fission(
            str(src), [], [], str(out), tool.FFMPEG, tool.ProcRegistry(),
            threading.Event(), ob_on=True, ob_count=2, threads=1)
        assert len(outs) == 2 and all(os.path.exists(p) for p in outs), outs
        h1 = Path(outs[0]).read_bytes()
        h2 = Path(outs[1]).read_bytes()
        assert h1 != h2, '裂变产物哈希必须互不相同'
        # 本地不可播放：ffprobe 拒读（Matroska 但分辨率字段被抹掉）
        probe = subprocess.run([tool.FFPROBE, '-v', 'error', outs[0]],
                               capture_output=True, text=True)
        assert probe.returncode != 0 or 'Invalid' in probe.stderr, '混淆产物应被 ffprobe 拒读'
    print('混淆裂变：命名、随机抽取、哈希唯一性、ffprobe 拒读检查通过')


def check_concat_no_obfuscate():
    """片头 + 主体 + 片尾（不混淆）应输出固定规格 720x1276 的标准 MP4，时长正确。"""
    def ff(args):
        return subprocess.run([tool.FFMPEG, '-v', 'error', '-y'] + args,
                              check=True, capture_output=True).stdout
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        main = root / 'main.mp4'
        head = root / 'head.mp4'
        tail = root / 'tail.mp4'
        ff(['-f', 'lavfi', '-i', 'color=red:size=320x240:rate=25', '-t', '2',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', str(main)])
        ff(['-f', 'lavfi', '-i', 'color=green:size=320x240:rate=25', '-t', '1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', str(head)])
        ff(['-f', 'lavfi', '-i', 'color=blue:size=320x240:rate=25', '-t', '1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', str(tail)])
        out = root / 'out'
        out.mkdir()
        outs = fission.process_one_fission(
            str(main), [str(head)], [str(tail)], str(out), tool.FFMPEG,
            tool.ProcRegistry(), threading.Event(),
            head_count=1, tail_count=1, ob_on=False, ob_count=1, threads=1)
        assert len(outs) == 1, outs
        data = tool._probe_json(outs[0])
        v = next(s for s in data['streams'] if s['codec_type'] == 'video')
        assert (v['width'], v['height']) == (720, 1276), (v['width'], v['height'])
        assert abs(float(v['duration']) - 4) < 0.2, v['duration']  # 1+2+1
    print('片头+主体+片尾拼接（不混淆）：固定 720x1276、时长正确检查通过')


def check_probe_and_run():
    """基础：probe_media 解析、_run 对坏输入抛错。"""
    with tempfile.TemporaryDirectory() as folder:
        src = Path(folder) / 'm.mp4'
        subprocess.run([tool.FFMPEG, '-v', 'error', '-y', '-f', 'lavfi',
                        '-i', 'color=red:size=320x240:rate=25', '-t', '1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-an', str(src)], check=True)
        info = tool.probe_media(str(src))
        assert (info['width'], info['height']) == (320, 240), info
        assert info['fps'] == 25.0 and not info['has_audio'], info
        assert 0 < info['duration'] <= 1.2, info['duration']
    try:
        tool._run([tool.FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
                   '-nostats', '-progress', 'pipe:1',
                   '-i', '/nonexistent-input.mp4', os.devnull], tool.ProcRegistry())
        raise AssertionError('坏输入必须抛错')
    except RuntimeError as e:
        assert 'nonexistent' in str(e), e
    print('probe_media 与 _run 错误处理检查通过')


if __name__ == '__main__':
    check_fission()
    check_concat_no_obfuscate()
    check_probe_and_run()
    print('全部回归检查通过')
