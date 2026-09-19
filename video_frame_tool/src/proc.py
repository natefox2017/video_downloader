"""可中断的子进程调用与取消检查。

ProcRegistry 统一登记进程以便「停止」能杀掉；_run() 读 -progress 输出时
必须另开线程排空 stderr，否则管道写满会死锁。"""

from concurrent.futures import CancelledError
import re
import subprocess
import threading

# ============================================================================
# 四、进程管理（可中断的 ffmpeg 调用）
# ============================================================================

class ProcRegistry:
    """
    记录当前正在运行的 ffmpeg 子进程，供"停止"按钮统一终止。
    线程安全：worker 线程会并发注册/注销。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._procs = set()

    def add(self, p):
        with self._lock:
            self._procs.add(p)

    def discard(self, p):
        with self._lock:
            self._procs.discard(p)

    def kill_all(self):
        """终止所有在跑的 ffmpeg（停止按钮调用）"""
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


# ffmpeg -progress 每秒左右吐一批 key=value，这里只认 out_time（已编码时长）。
# 不用 out_time_us / out_time_ms：这两个字段的单位在不同 ffmpeg 版本里不一致
# （历史上 out_time_ms 实际是微秒），踩过一次就不碰了。
_PROGRESS_TIME_RE = re.compile(rb"^out_time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def _read_out_time(line, expected_dur):
    """
    把 ffmpeg -progress 的一行折成"真实完成度"（0~1）；看不懂的行返回 None。

    这是整条流水线里**唯一**的真实进度来源：ffmpeg 自己编码了多少秒，
    再除以这条成片的时长，就是这条视频此刻真正的完成度。
    """
    if line.startswith(b"progress="):
        return 1.0 if line.strip() == b"progress=end" else None
    m = _PROGRESS_TIME_RE.match(line)
    if not m or expected_dur <= 0:
        return None
    secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return min(1.0, secs / expected_dur)


def _run(cmd, registry, on_progress=None, expected_dur=0.0):
    """
    执行一条 ffmpeg 命令并等待结束。

    - 进程会注册到 registry，便于用户中途停止
    - 非 0 退出时抛出 RuntimeError，附带 stderr 最后几行，方便定位
    - 传了 on_progress（命令里要带 -progress pipe:1）时，
      边跑边把 ffmpeg 汇报的真实编码进度折算成 0~1 回调出去
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    registry.add(p)
    err_lines = []

    def drain_err():
        """stderr 必须一直读走：管道写满会把 ffmpeg 卡死在写日志上"""
        try:
            for line in p.stderr:
                err_lines.append(line)
        except Exception:
            pass

    err_thread = threading.Thread(target=drain_err, daemon=True)
    err_thread.start()
    try:
        if on_progress is not None and expected_dur > 0:
            for line in p.stdout:              # -progress pipe:1 的逐行输出
                frac = _read_out_time(line, expected_dur)
                if frac is None:
                    continue
                try:
                    on_progress(frac)
                except Exception:
                    pass
        else:
            p.stdout.read()                    # 不要进度也要读空，避免管道写满
        p.wait()
    finally:
        registry.discard(p)
        err_thread.join(timeout=3)
        for stream in (p.stdout, p.stderr):
            try:
                stream.close()
            except Exception:
                pass
    if p.returncode != 0:
        tail = b"".join(err_lines).decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError(" | ".join(tail[-3:])[:400] if tail else "ffmpeg 返回非 0")


def _check_stopped(opts):
    """停止后不再启动新转码，也不把取消当作坏素材重新编码。"""
    event = opts.get("stop_event")
    if event is not None and event.is_set():
        raise CancelledError("处理已停止")


def fmt_duration(seconds):
    """把秒数格式化成"1 分 25 秒"这类便于阅读的形式（日志是给普通用户看的）。"""
    try:
        s = max(0, int(round(float(seconds))))
    except Exception:
        return "—"
    if s < 60:
        return f"{s} 秒"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} 分 {s} 秒"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"
