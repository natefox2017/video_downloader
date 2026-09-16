"""进度折算。

把「低清副本 15% / 画中画 25% / 合成 60%」（无画中画时合成独占）折成单条视频完成度。
**只吃真实数字**：禁止再引入"估算比例 + 定时器自己往前爬"的进度动画。"""

class VideoProgress:
    """
    把一条视频**各阶段的真实完成量**折算成 0~1 的总完成度，供进度条使用。

    这里刻意不做"按时间猜"的平滑动画：只有真的干完了一步，进度才往前走。
    所以进度条不会跑在事实前面，任务卡住时也不会自己偷偷往前爬。

    三个阶段的完成量全部是实测数字，没有一个估计值：
        副本   —— prepare_small_pool 报的「已生成 / 需生成」个数
        画中画 —— _encode_pip_batches 报的「已完成批数 / 总批数」
        合成   —— ffmpeg 用 -progress 自己报的 out_time / 成片时长
    阶段之间按经验耗时占比加权（合成最重）；没开画中画时只剩合成一个阶段，
    权重自动归一，不会在开头留出一段走不动的空档。
    """

    _PIP_PLAN = (("copy", 0.15), ("pip", 0.25), ("compose", 0.60))
    _PLAIN_PLAN = (("compose", 1.0),)

    def __init__(self, emit, pip=True):
        """
        :param emit: 完成度回调（0~1），由界面侧换算成整批进度
        :param pip : 这条视频是否要走画中画（决定阶段权重表）
        """
        self._emit = emit
        self._plan = self._PIP_PLAN if pip else self._PLAIN_PLAN
        self._frac = {name: 0.0 for name, _ in self._plan}
        self._sent = -1.0

    def phase(self, name, frac):
        """记录某阶段的真实完成度；只增不减（并发下晚到的旧值不许把进度拽回去）"""
        if name not in self._frac:
            return
        self._frac[name] = max(self._frac[name], min(1.0, max(0.0, float(frac))))
        self._push()

    def count(self, name, done, total):
        """按个数记进度（副本数 / 批次数）；总数为 0 表示该阶段无事可做，直接算完成"""
        self.phase(name, (float(done) / total) if total else 1.0)

    def finish(self):
        """收尾：把还没报满的阶段补到 100%（这条视频确实已经处理完了）"""
        for name in self._frac:
            self._frac[name] = 1.0
        self._push()

    def _push(self):
        """完成度每变动 0.2% 才发一条消息，避免刷爆消息队列"""
        total = sum(weight for _, weight in self._plan)
        frac = sum(weight * self._frac[name] for name, weight in self._plan) / total
        if frac - self._sent < 0.002 and frac < 1.0:
            return
        self._sent = frac
        try:
            self._emit(frac)
        except Exception:
            pass
