"""回归检查：缓存并发、增量扫描、真实进度、取消语义与成片像素。

只用标准库与已安装的 ffmpeg，所有素材仅写入临时目录，不碰用户素材。
"""
import os
import json
import random
import queue
import sys
from types import SimpleNamespace
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

# 直接跑 `python3 tests/test_video_frame_tool.py` 时把 src 加进 sys.path；
# 已经 `pip install -e .` 的环境不受影响。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import video_frame_tool as tool
from video_frame_tool import pip_track, pipeline, probe, small_pool
from video_frame_tool.ui import window as ui_window


class MockWidget(dict):
    """记录控件被刷新的参数与次数，无需启动真实 Tk 窗口。"""
    def __init__(self):
        super().__init__(value=0)
        self.updates = 0
        self.values = []

    def configure(self, **kwargs):
        self.updates += 1
        if 'value' in kwargs:
            self.values.append(kwargs['value'])
        self.update(kwargs)


class FakeApp:
    """
    把 App 上与消息泵/进度/任务调度相关的**真实方法**挂到替身上，
    只把 Tk 控件换成可断言的字典——这样测的还是产品代码，不是为测试重写的逻辑。
    """
    _percent = staticmethod(tool.App._percent)
    _v_percent = tool.App._v_percent
    _draw_progress = tool.App._draw_progress
    _set_progress_text = tool.App._set_progress_text
    _poll_queue = tool.App._poll_queue
    _finish = tool.App._finish
    _finish_small = tool.App._finish_small
    _finish_small_error = tool.App._finish_small_error
    _worker = tool.App._worker
    _small_worker = tool.App._small_worker
    _log_q = tool.App._log_q
    _open_small = tool.App._open_small

    def __init__(self, **state):
        self.msg_q = queue.Queue()
        self.progress = MockWidget()
        self.progress_text = MockWidget()
        self.btn_start = MockWidget()
        self.btn_stop = MockWidget()
        self.btn_prep = MockWidget()
        self.stop_event = threading.Event()
        self.registry = tool.ProcRegistry()
        self.running = True
        self._v_weights, self._v_done, self._v_total = [], {}, 0.0
        self._batch_done = self._batch_total = 0
        self.after = lambda *args: 1
        self._log = lambda text: None
        self._log_batch = lambda lines: None
        self._finish_error = lambda payload: None
        self.__dict__.update(state)


def check_small_cache():
    """同目录并发只生成一次；缓存复用、失效重建、停止及成片规格均可验证。"""
    with tempfile.TemporaryDirectory() as folder:
        src = str(Path(folder) / 'source.mp4')
        subprocess.run([
            tool.FFMPEG, '-v', 'error', '-y', '-f', 'lavfi',
            '-i', 'testsrc2=size=640x360:rate=25', '-t', '2',
            '-an', '-c:v', 'libx264', '-threads', '1', src,
        ], check=True)
        pool = [dict(path=src, dur=2, w=640, h=360)]
        started = threading.Event()
        calls = []
        original = small_pool._build_small_copy

        def build(*args):
            """记录真实转码次数，并留出第二个任务进入缓存流程的时间。"""
            calls.append(args[1])
            started.set()
            time.sleep(0.1)
            return original(*args)

        with patch.object(small_pool, '_build_small_copy', build):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(tool.prepare_small_pool, pool, 180,
                                        hw=tool.HWACCEL_OFF)
                assert started.wait(10), '副本转码没有启动'
                second = executor.submit(tool.prepare_small_pool, pool, 180,
                                         hw=tool.HWACCEL_OFF)
                a, b = first.result(), second.result()
            assert a == b and src in a, '并发任务必须复用同一份有效副本'
            assert len(calls) == 1, '同一素材被重复转码'
            info = tool.probe_media(a[src])
            assert (info['width'], info['height']) == (320, 180)
            assert abs(info['duration'] - 2) < 0.05 and not info['has_audio']
            data = tool._probe_json(a[src])
            assert int(data['streams'][0]['nb_frames']) == 50
            assert tool.prepare_small_pool(pool, 180) == a
            assert len(calls) == 1, '命中缓存不应转码'
            stat = os.stat(src)
            os.utime(src, (stat.st_atime, stat.st_mtime + 2))
            assert tool.prepare_small_pool(pool, 180, hw=tool.HWACCEL_OFF) == a
            assert len(calls) == 2, '源素材变更应重新生成副本'
            meta_path = Path(folder) / tool.PIP_SMALL_DIR / tool.PIP_SMALL_META
            meta = json.loads(meta_path.read_text())
            meta['v'] = tool.PIP_SMALL_V - 1
            meta_path.write_text(json.dumps(meta))
            assert tool.prepare_small_pool(pool, 180, hw=tool.HWACCEL_OFF) == a
            assert len(calls) == 3
            assert tool.prepare_small_pool(pool, 180) == a
            assert len(calls) == 3, '版本升级只能重建一次'
            stamp = meta_path.stat().st_mtime_ns
            assert tool.prepare_small_pool(pool, 180) == a
            assert meta_path.stat().st_mtime_ns == stamp, '热缓存不应重写台账'
            stat = os.stat(src)
            os.utime(src, (stat.st_atime, stat.st_mtime + 2))
            with patch.object(small_pool, '_build_small_copy', return_value=None):
                assert tool.prepare_small_pool(pool, 180) == {}, '生成失败不能返回失效旧副本'
                assert tool.prepare_small_pool(pool, 180) == {}, '下次也不能认领失败旧副本'
            assert Path(a[src]).exists(), '失败不能删除长期缓存'
            stopped = threading.Event()
            stopped.set()
            assert tool.prepare_small_pool(pool, 180, stop_event=stopped) == {}
            assert len(calls) == 3
        assert pool == [dict(path=src, dur=2, w=640, h=360)]
    print('低清缓存并发、复用、失效、停止、帧数及媒体规格检查通过')



def check_incremental_scan():
    """增加/修改一个素材只探测该文件，共享素材列表不能被规划器打乱。"""
    with tempfile.TemporaryDirectory() as folder:
        for name in ('a.mp4', 'b.mp4', 'c.mp4'):
            (Path(folder) / name).write_bytes(b'source')
        calls = []
        def probe(path):
            """扫描只需要元数据，记录真正需要探测的路径。"""
            calls.append(path)
            return 5, 640, 360
        with patch.object(small_pool, 'probe_dur_size', probe):
            pool = tool.scan_pip_pool(folder)
            assert len(calls) == 3
            (Path(folder) / 'd.mp4').write_bytes(b'new')
            pool = tool.scan_pip_pool(folder)
            assert len(calls) == 4, '增加一个文件不应重新探测整个素材池'
            (Path(folder) / 'a.mp4').write_bytes(b'changed')
            tool._POOL_MEM.clear()  # 模拟重启，从磁盘缓存增量更新。
            pool = tool.scan_pip_pool(folder)
            assert len(calls) == 5, '重启后仍应只探测变更文件'
        before = list(pool)
        main = dict(duration=8, width=640, height=360, fps_str='25')
        opts = dict(pip_w=.24, pip_h=.2, pip_fill='crop', pip_speed=1.2,
                    pip_head=.1, pip_tail=.1, rnd_zoom=1.06, rnd_flip_h=True,
                    rnd_color=True)
        random.seed(42)
        tool._plan_pip_segments(main, opts, pool)
        assert pool == before, '不能打乱被其它视频共享的素材列表'


def check_image_probe_cache():
    """同一张图并发只探测一次，文件改变后获取新尺寸。"""
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / 'image.ppm'
        image.write_bytes(b'P6\n2 2\n255\n' + b'\xff\x00\x00' * 4)
        original = probe._probe_json
        # 注意别用 probe 当 as 目标：会遮蔽上面 import 的 probe 子模块
        with patch.object(probe, '_probe_json', wraps=original) as spy:
            with ThreadPoolExecutor(4) as executor:
                sizes = list(executor.map(tool.probe_image_size, [str(image)] * 8))
            assert sizes == [(2, 2)] * 8
            assert spy.call_count == 1, '同一产品图不应重复启动 ffprobe'
            image.write_bytes(b'P6\n4 2\n255\n' + b'\xff\x00\x00' * 8)
            assert tool.probe_image_size(str(image)) == (4, 2)
            assert spy.call_count == 2


def check_cancelled_batch():
    """停止造成的失败不能拆成逐段重试，已取消的单视频也不能启动探测。"""
    stopped = threading.Event()
    opts = dict(stop_event=stopped)
    def cancel(*args):
        """模拟停止按钮终止正在运行的 ffmpeg。"""
        stopped.set()
        raise RuntimeError('terminated')
    with tempfile.TemporaryDirectory() as folder:
        with patch.object(pip_track, '_encode_pip_batch', side_effect=cancel) as encode:
            try:
                tool._encode_pip_batches([{}] * 8, folder, opts, None, lambda msg: None)
                raise AssertionError('取消必须中止批次')
            except tool.CancelledError:
                pass
            assert encode.call_count == 1
        with patch.object(pipeline, 'probe_media', side_effect=AssertionError('不应探测')):
            try:
                tool.process_one('unused', folder, opts, None, lambda msg: None)
                raise AssertionError('取消必须中止单视频')
            except tool.CancelledError:
                pass


def check_subset_names():
    """同主名、不同扩展名的素材在单条/整池请求中使用同一副本路径。"""
    with tempfile.TemporaryDirectory() as folder:
        pool = []
        for name in ('clip.mp4', 'clip.mov'):
            path = Path(folder) / name
            path.write_bytes(b'source')
            pool.append(dict(path=str(path)))
        small = tool.small_dir_of(folder)
        full = tool._small_plan(pool, small, 480)[0]
        for item in pool:
            assert tool._small_plan([item], small, 480)[0][item['path']] == full[item['path']]
        assert len(set(full.values())) == 2


def check_ui_queue():
    """
    进度条只吃真实数字：一轮消息只刷一次、静默时不许自己往前爬、
    预生成副本走真实个数、预生成失败必须发出恢复界面的事件。
    """
    app = FakeApp()
    app.msg_q.put(('vtotal', ([10.0, 5.0, 5.0], 20.0)))
    for i in range(101):
        app.msg_q.put(('vprogress', (0, 10.0 * i / 100)))
    tool.App._poll_queue(app)
    # 最长那条（10 秒）跑完了，占全部工作量（20 秒）的一半
    assert app.progress['value'] == 50.0, app.progress['value']
    # 101 条高频消息只画了两次：批次开始时归零 + 本轮的真实值，不是逐条刷屏
    assert app.progress.values == [0, 50.0], app.progress.values
    assert app.progress_text['text'] == '50%', app.progress_text
    assert app.progress_text.updates == 1, '进度值也只该刷一次'
    # 没有新消息时进度必须原地不动——旧版"估算动画"最不准的地方就在这
    tool.App._poll_queue(app)
    assert app.progress['value'] == 50.0 and app.progress.values == [0, 50.0]
    # 进度不许回退
    app.msg_q.put(('vprogress', (0, 1.0)))
    tool.App._poll_queue(app)
    assert app.progress['value'] == 50.0, app.progress['value']
    # 预生成低清副本走真实 已完成/总数
    app.msg_q.put(('total', 8))
    app.msg_q.put(('progress', (6, 8)))
    tool.App._poll_queue(app)
    assert app.progress['value'] == 75.0, app.progress['value']
    assert app.progress_text['text'] == '75%', app.progress_text
    # 预生成失败必须发事件，让按钮恢复可点
    app2 = FakeApp()
    app2._log_q = lambda msg: app2.msg_q.put(('log', msg))
    with patch.object(ui_window, '_safe_probe', return_value=None):
        tool.App._small_worker(app2, 'unused', ['bad.mp4'], {})
    events = []
    while not app2.msg_q.empty():
        events.append(app2.msg_q.get())
    assert any(kind == 'small_error' for kind, _ in events), events


def check_progress_phase_math():
    """一条视频的完成度按阶段加权折算，只有真干完一步才往前走。"""
    seen = []
    prog = tool.VideoProgress(seen.append, pip=True)
    assert seen == [], '刚建好不该上报'
    prog.count('copy', 0, 10)
    assert seen[-1] == 0.0, seen
    prog.count('copy', 10, 10)
    assert abs(seen[-1] - .15) < 1e-9, seen
    prog.count('pip', 5, 10)
    assert abs(seen[-1] - .275) < 1e-9, seen
    prog.phase('compose', .5)
    assert abs(seen[-1] - .575) < 1e-9, seen
    prog.phase('compose', .1)
    assert seen[-1] >= .575 - 1e-9, '旧值不许把进度拽回去'
    prog.finish()
    assert abs(seen[-1] - 1.0) < 1e-9, seen
    # 不开画中画时，合成独占全部权重
    plain = []
    tool.VideoProgress(plain.append, pip=False).phase('compose', .4)
    assert abs(plain[-1] - .4) < 1e-9, plain


def check_run_progress():
    """ffmpeg 的 -progress 输出能折成真实完成度，失败时仍带得出原因。"""
    parse = tool._read_out_time
    assert abs(parse(b'out_time=00:00:04.000000\n', 8) - .5) < 1e-9
    assert parse(b'out_time=N/A\n', 8) is None, '开头的 N/A 不能当成 0 秒'
    assert parse(b'progress=end\n', 8) == 1.0
    assert parse(b'progress=continue\n', 8) is None
    assert parse(b'frame=120\nfps=30\n', 8) is None, '别的字段不许干扰'
    assert parse(b'out_time=00:00:99.0\n', 8) == 1.0, '超过时长也要夹到 100%'
    assert parse(b'out_time=00:00:04.0\n', 0) is None, '时长未知就不许瞎报'
    assert parse(b'out_time=00:00:04.0\n', -1) is None
    try:
        tool._run([tool.FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
                   '-nostats', '-progress', 'pipe:1',
                   '-i', '/nonexistent-input.mp4', os.devnull],
                  tool.ProcRegistry())
        raise AssertionError('坏输入必须抛错')
    except RuntimeError as error:
        assert 'nonexistent' in str(error), error


def check_waiting_cancel():
    """等待另一个缓存任务持锁时，停止也能退出，不必等完整素材池。"""
    with tempfile.TemporaryDirectory() as folder:
        lock = tool._folder_lock(os.path.realpath(tool.small_dir_of(folder)))
        stopped = threading.Event()
        lock.acquire()
        try:
            with ThreadPoolExecutor(1) as executor:
                pending = executor.submit(tool.prepare_small_pool,
                                          [dict(path=os.path.join(folder, 'clip.mp4'))],
                                          480, stop_event=stopped)
                stopped.set()
                assert pending.result(timeout=2) == {}
        finally:
            lock.release()


def check_output_pixels():
    """真实合成检查首帧、产品图、画中画、音轨和视频帧数，素材仅保存在临时目录。"""
    def ff(args):
        """执行测试用 FFmpeg，失败时立即停止检查。"""
        return subprocess.run([tool.FFMPEG, '-v', 'error', '-y'] + args,
                              check=True, capture_output=True).stdout
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        pip_dir = root / 'pip'
        pip_dir.mkdir()
        ff(['-f', 'lavfi', '-i', 'color=blue:size=320x240:rate=25',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
            '-t', '2', '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', str(root / 'main.mp4')])
        ff(['-f', 'lavfi', '-i', 'color=lime:size=160x120:rate=25',
            '-t', '3', '-c:v', 'libx264', '-threads', '1', str(pip_dir / 'clip.mp4')])
        for name, color in [('cover', b'\xff\x00\x00'), ('product', b'\xff\xff\xff')]:
            (root / (name + '.ppm')).write_bytes(b'P6\n32 32\n255\n' + color * 1024)
        opts = dict(cover=str(root / 'cover.ppm'), product=str(root / 'product.ppm'),
                    prod_size=(32, 32), pip_dir=str(pip_dir), pip_w=.24, pip_h=.2,
                    pip_right=.08, pip_top=.09, pip_fill='crop', pip_speed=1.2,
                    pip_head=.1, pip_tail=.1, rnd_zoom=1, rnd_flip_h=False,
                    rnd_color=False, rnd_jitter=0, pip_small=True,
                    preset='veryfast', prod_start=10, hwaccel=tool.HWACCEL_OFF,
                    threads=2, pip_parallel=1)
        out = root / 'out'
        out.mkdir()
        notes = []
        target = tool.process_one(str(root / 'main.mp4'), str(out), opts,
                                  tool.ProcRegistry(), notes.append)
        assert any('完成' in line and '用时' in line for line in notes), notes
        data = tool._probe_json(target)
        video = next(stream for stream in data['streams'] if stream['codec_type'] == 'video')
        assert (video['width'], video['height'], int(video['nb_frames'])) == (320, 240, 50)
        assert abs(float(video['duration']) - 2) < .05
        assert sum(stream['codec_type'] == 'audio' for stream in data['streams']) == 1
        raw = ff(['-i', target, '-map', '0:v:0', '-vsync', '0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
        def pixel(frame, x, y):
            """读取一帧中的 RGB 像素。"""
            offset = (frame * 320 * 240 + y * 320 + x) * 3
            return raw[offset:offset + 3]
        assert pixel(0, 10, 10)[0] > 230
        assert pixel(5, 10, 10)[2] > 230
        assert pixel(5, 160, 180)[0] < 25
        assert min(pixel(20, 160, 180)) > 230
        assert pixel(20, 250, 40)[1] > 230


def check_opts_contract():
    """_collect_opts / _snapshot 对两个新开关的处理（不启动 Tk 窗口）。"""
    def var(value):
        """构造一个只会返回固定值的假控件变量。"""
        return SimpleNamespace(get=lambda: value)
    app = tool.App.__new__(tool.App)          # 绕过 __init__，直接测参数汇总
    app.settings = {'pip': {'small': True}}
    app.cover_var, app.product_var, app.dir_var = var('/c'), var('/p'), var('/v')
    app.pip_var = var('/pip')
    app.prod_start_var, app.prod_chance_var = var('60'), var('40')
    app.pip_w, app.pip_h = var('24'), var('20')
    app.pip_right, app.pip_top, app.pip_fill = var('8'), var('9'), var('裁剪填满')
    app.pip_head, app.pip_tail, app.pip_speed = var('10'), var('10'), var('1.2')
    app.rnd_flip_h, app.rnd_zoom, app.rnd_color, app.rnd_jitter = \
        var(True), var('1.06'), var(True), var('3')
    app.preset_var, app.workers_var, app.hwaccel_var = var('快速'), var('5'), var('自动')
    app.stop_event = threading.Event()
    app.pip_on = var(True)
    on = tool.App._collect_opts(app)
    assert on['pip_dir'] == '/pip' and on['pip_on'] is True, on
    assert on['prod_chance'] == 40 and on['threads'] >= 1, on
    app.pip_on = var(False)                   # 关掉开关：画中画目录一律视为未启用
    off = tool.App._collect_opts(app)
    assert off['pip_dir'] is None and off['pip_on'] is False, off
    app.prod_chance_var = var('')             # 输入框被清空时回落默认值，不能抛异常
    assert tool.App._collect_opts(app)['prod_chance'] == tool.PRODUCT_CHANCE
    snap = tool.App._snapshot(app)
    assert snap['pip']['enabled'] is False, snap['pip']
    assert snap['run']['prod_chance'] == tool.PRODUCT_CHANCE, snap['run']


def check_toggles():
    """商品图概率与画中画开关：都要真实生效，且互不干扰（含可选输入导致的索引前移）。"""
    def ff(args):
        """执行测试用 FFmpeg，失败时立即停止检查。"""
        return subprocess.run([tool.FFMPEG, '-v', 'error', '-y'] + args,
                              check=True, capture_output=True).stdout
    # ---- 命令级：概率决定"要不要那一路输入、那一段 overlay" ----
    info = dict(width=320, height=240, fps_str='25', has_audio=False,
                acodecs=(), duration=2.0)
    base = dict(cover='/no/cover.png', product='/no/product.png', prod_size=(32, 32),
                prod_start=10, rnd_jitter=0, pip_right=.08, pip_top=.09,
                preset='veryfast', threads=1, hwaccel=tool.HWACCEL_OFF)
    pip = ('/no/pip.mp4', 64, 48)

    def fc_of(command):
        """取出命令里的滤镜链，便于断言输入索引。"""
        return command[command.index('-filter_complex') + 1]

    off = tool._decide_product(dict(base, prod_chance=0))
    assert off['product'] == '' and off['prod_size'] is None, off
    command, _, _ = tool.build_command('main.mp4', off, info, 'o.mp4', pip)
    assert '/no/product.png' not in command, command
    # 商品图缺席时画中画要前移到 [2:v]；写死 [2:v] 的话这里会指到画中画上
    assert '[prod]' not in fc_of(command) and '[2:v]' in fc_of(command), fc_of(command)

    on = tool._decide_product(dict(base, prod_chance=100))
    assert on['product'] == '/no/product.png', on
    command, _, _ = tool.build_command('main.mp4', on, info, 'o.mp4', pip)
    assert '/no/product.png' in command and '[3:v]' in fc_of(command), fc_of(command)

    with patch.object(random, 'random', return_value=0.1):       # 10 < 40 → 命中
        assert tool._decide_product(dict(base, prod_chance=40))['product'] == '/no/product.png'
    with patch.object(random, 'random', return_value=0.9):       # 90 >= 40 → 不命中
        miss = tool._decide_product(dict(base, prod_chance=40))
    assert miss['product'] == '' and miss['product_files'] == [], miss
    shared = dict(base, prod_chance=0)                           # 同批视频共享同一份 opts
    tool._decide_product(shared)
    assert shared['product'] == '/no/product.png', '不能就地改共享的 opts'

    # ---- 成片级：像素位置必须对得上（输入索引算错会在这里暴露） ----
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        pip_dir = root / 'pip'
        pip_dir.mkdir()
        ff(['-f', 'lavfi', '-i', 'color=blue:size=320x240:rate=25',
            '-t', '2', '-c:v', 'libx264', '-threads', '1', str(root / 'main.mp4')])
        ff(['-f', 'lavfi', '-i', 'color=lime:size=160x120:rate=25',
            '-t', '3', '-c:v', 'libx264', '-threads', '1', str(pip_dir / 'clip.mp4')])
        for name, color in [('cover', b'\xff\x00\x00'), ('product', b'\xff\xff\xff')]:
            (root / (name + '.ppm')).write_bytes(b'P6\n32 32\n255\n' + color * 1024)
        common = dict(cover=str(root / 'cover.ppm'), product=str(root / 'product.ppm'),
                      prod_size=(32, 32), pip_w=.24, pip_h=.2, pip_right=.08, pip_top=.09,
                      pip_fill='crop', pip_speed=1.2, pip_head=.1, pip_tail=.1,
                      rnd_zoom=1, rnd_flip_h=False, rnd_color=False, rnd_jitter=0,
                      pip_small=True, preset='veryfast', prod_start=10,
                      hwaccel=tool.HWACCEL_OFF, threads=2, pip_parallel=1)
        out = root / 'out'
        out.mkdir()

        def render(**extra):
            """按给定开关跑一次真实合成，返回整条成片的 RGB 原始帧。"""
            notes = []
            opts = dict(common, **extra)
            target = tool.process_one(str(root / 'main.mp4'), str(out), opts,
                                      tool.ProcRegistry(), notes.append)
            assert any('完成' in line for line in notes), notes
            return ff(['-i', target, '-map', '0:v:0', '-vsync', '0',
                       '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])

        def pixel(raw, frame, x, y):
            """读取某帧某坐标的 RGB 值。"""
            offset = (frame * 320 * 240 + y * 320 + x) * 3
            return raw[offset:offset + 3]

        # A：概率 0 + 开画中画 → 商品图位置是主画面底色，画中画照常贴在右上
        raw = render(pip_dir=str(pip_dir), prod_chance=0)
        assert len(raw) == 50 * 320 * 240 * 3, '帧数应为 50'
        assert pixel(raw, 20, 160, 180)[2] > 230, '概率 0 时不该出现商品图'
        assert pixel(raw, 20, 250, 40)[1] > 230, '不加商品图不能把画中画挤掉'

        # B：概率 100 + 关画中画 → 商品图照常叠加，且不生成画中画轨
        with patch.object(pipeline, 'build_pip_track',
                          side_effect=AssertionError('画中画已关闭，不应调用')):
            raw = render(pip_dir=None, prod_chance=100)
        assert min(pixel(raw, 20, 160, 180)) > 230, '商品图应该照常叠加'
        assert pixel(raw, 20, 250, 40)[2] > 230, '画中画位置应回到主画面底色'


def check_warm_yields():
    """取消预热后只等已运行的两个任务，前台可拿锁，余下素材不再转码。"""
    with tempfile.TemporaryDirectory() as folder:
        pool = []
        for i in range(4):
            path = Path(folder) / ('clip%d.mp4' % i)
            path.write_bytes(b'source')
            pool.append(dict(path=str(path), dur=2, w=640, h=360))
        stopped, started, release = threading.Event(), threading.Event(), threading.Event()
        calls, guard = [], threading.Lock()
        def build(item, dst, short, hw):
            """暂停两个已运行的转码，模拟用户在此时启动前台任务。"""
            with guard:
                calls.append(item['path'])
                if len(calls) == 2:
                    started.set()
            assert release.wait(2)
            Path(dst).write_bytes(b'x' * 5000)
            return True
        opts = dict(stop_event=stopped, pip_w=.24, pip_h=.2, rnd_zoom=1)
        with patch.object(small_pool, 'scan_pip_pool', return_value=pool), \
                patch.object(small_pool, '_build_small_copy', side_effect=build):
            tool._warm_small_pool_async(folder, dict(width=320, height=240), opts)
            try:
                assert started.wait(2), '预热没有启动'
                stopped.set()
                with ThreadPoolExecutor(1) as executor:
                    foreground = executor.submit(tool.prepare_small_pool, pool[:1], 480)
                    release.set()
                    assert pool[0]['path'] in foreground.result(timeout=2)
                assert len(calls) == 2, '停止预热后不应再转码剩余素材'
            finally:
                release.set()

def check_compact_ui():
    """检查目录入口、停止计数与按钮恢复，不读取或改写用户素材。"""
    logs = []
    app = FakeApp(_log=logs.append, _batch_total=3)
    app.stop_event.set()
    # 待运行任务通过 CancelledError 退出，不能让 as_completed 永远等不到通知。
    with patch.object(ui_window, '_safe_probe', return_value=None), \
            patch.object(ui_window, 'process_one', side_effect=tool.CancelledError):
        tool.App._worker(app, ['a.mp4', 'b.mp4', 'c.mp4'], 'unused', {'workers': 1})
    events = list(app.msg_q.queue)
    payload = next(value for kind, value in events if kind == 'done')
    assert payload[:2] == (0, 0)
    tool.App._finish(app, payload)
    assert not app.running and app.btn_start == dict(value=0, text='开始处理', state='normal')
    assert app.btn_stop['state'] == 'disabled' and app.btn_stop['text'] == '已完成'
    assert app.progress_text['text'] == '已停止', app.progress_text
    logs = [text for kind, text in events if kind == 'log'] + logs
    assert any('已停止' in text for text in logs), logs
    with tempfile.TemporaryDirectory() as folder:
        app.pip_var = SimpleNamespace(get=lambda: folder)
        path = tool.small_dir_of(folder)
        with patch.object(ui_window, 'open_folder', return_value=True) as opened, \
                patch.object(ui_window.messagebox, 'showinfo') as info:
            tool.App._open_small(app)
            assert not os.path.exists(path) and path in info.call_args.args[1]
            Path(path).mkdir()
            tool.App._open_small(app)
            opened.assert_called_once_with(path)
        src = Path(folder) / 'clip.mp4'
        src.write_bytes(b'source')
        pool = [dict(path=str(src), dur=2, w=640, h=360)]
        def build(item, dst, short, hw):
            """模拟有效的缓存产物。"""
            Path(dst).write_bytes(b'x' * 5000)
            return True
        with patch.object(small_pool, '_build_small_copy', side_effect=build):
            tool.prepare_small_pool(pool, 480)
        progress = []
        tool.prepare_small_pool(pool, 480, progress_cb=lambda n, m: progress.append((n, m)))
        assert progress == [(1, 1)]

def check_native_layout():
    """可选 Tk 窗口验收：布局不越界、主按钮状态恢复、所有原参数保留。"""
    with patch.object(tool.App, '_save_settings', lambda self: None):
        app = tool.App()
        try:
            # 自适应尺寸必须整窗落在屏幕内，否则底部会被菜单栏 / Dock 切掉（用户反馈过"没显示全"）
            app.update()
            assert app.winfo_width() <= app.winfo_screenwidth() - 40, app.winfo_width()
            assert app.winfo_height() <= app.winfo_screenheight() - 40, app.winfo_height()
            for size in ('1080x880', '1000x830', '900x680'):
                app.geometry(size)
                app.update()
                assert app.log_box.winfo_height() >= 40
                pending = list(app.winfo_children())
                labels = []
                while pending:
                    widget = pending.pop()
                    pending.extend(widget.winfo_children())
                    if not widget.winfo_ismapped():
                        continue
                    assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width() + 2
                    assert widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + app.winfo_height() + 2
                    if 'text' in widget.keys():
                        labels.append(str(widget.cget('text')))
                assert '画中画目录' in labels and '小视频目录' not in labels
                assert '打开副本目录' in labels and '整机 CPU' in labels
                # 进度值贴在长进度条右边（同一行、更靠右），不独占一行
                assert app.progress_text.winfo_rootx() > app.progress.winfo_rootx() + app.progress.winfo_width()
                assert abs(app.progress_text.winfo_rooty() - app.progress.winfo_rooty()) < 24
                # 资源监控与「处理/停止」按钮同一行并靠右
                assert abs(app.mon_cpu.winfo_rooty() - app.btn_start.winfo_rooty()) < 24
                assert app.mon_cpu.winfo_rootx() > app.btn_stop.winfo_rootx()
                assert app.mon_mem_text.winfo_rootx() + app.mon_mem_text.winfo_width() \
                    <= app.winfo_rootx() + app.winfo_width()
                # 内存数值变长不许被裁掉（用户反馈过"内存大小有变被盖着了"）
                default_font = tool.tkfont.nametofont('TkDefaultFont')
                for widget, sample in ((app.mon_mem_text, '100%　1024.0/1024.0 GiB'),
                                       (app.mon_cpu_text, '100%')):
                    widget.configure(text=sample)
                    app.update()
                    need = default_font.measure(sample)
                    assert widget.winfo_width() >= need, (sample, widget.winfo_width(), need)
                app.mon_mem_text.configure(text='—')
            app.running = True
            app.stop_event.clear()
            app.btn_start.configure(text='处理中…', state='disabled')
            assert app.btn_start.instate(['disabled'])
            app._draw_progress(42.0)
            assert app.progress_text['text'] == '42%'
            app._finish((2, 0, 49, 1024))
            assert app.btn_start.instate(['!disabled'])
            assert app.btn_start['text'] == '开始处理'
            assert float(app.progress['value']) == 100.0
            assert app.progress_text['text'] == '全部完成'
        finally:
            app._on_close()

if __name__ == '__main__':
    check_compact_ui()
    check_small_cache()
    check_incremental_scan()
    check_image_probe_cache()
    check_cancelled_batch()
    check_subset_names()
    check_ui_queue()
    check_progress_phase_math()
    check_run_progress()
    check_waiting_cancel()
    check_output_pixels()
    check_opts_contract()
    check_toggles()
    check_warm_yields()
    if '--ui' in __import__('sys').argv:
        check_native_layout()
    print('全部回归检查通过')
