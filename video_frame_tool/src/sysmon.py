"""系统资源监控。

整机 CPU / 内存采样，只用各平台原生廉价接口，不依赖 psutil，也不做阻塞界面的操作。"""

import ctypes
import os

from .platform_compat import PLATFORM, PLATFORM_LINUX, PLATFORM_MACOS, PLATFORM_WINDOWS

# ============================================================================
# 一·六、系统资源监控（CPU / 内存 / 本进程占用）
# ============================================================================
# 界面底部实时显示 CPU 与内存占用。实现原则：
#   1. **不引入第三方依赖**（不用 psutil）：Linux 读 procfs，
#      macOS 用 ctypes 调 mach 内核接口，Windows 调 Win32 API；
#   2. 采样必须便宜且不阻塞界面：上述接口都是微秒级，每秒采样一次；
#   3. 任何一步失败都只影响这一项显示（值为 None → 界面显示 "—"），绝不抛异常。
#
# 名词说明：
#   cpu       —— 全系统 CPU 使用率 0~100%（所有核心合计的忙碌比例），需要两次采样算差值
#   mem_used  —— 系统已用内存，口径对齐 macOS「活动监视器」的"已用内存"
#                = (匿名页 - 可回收页) + 常驻页(wired) + 压缩页
#   self_rss  —— 本进程常驻内存（RSS）
# ============================================================================

def fmt_bytes(n):
    """把字节数格式化成人类可读字符串；None → "—"（表示该平台取不到）"""
    if n is None:
        return "—"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


def fmt_duration(seconds):
    """把秒数格式化成"1 分 25 秒"这类便于阅读的形式（日志是给普通用户看的）"""
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


class _LinuxResReader:
    """Linux：读 procfs。内核直接导出，无需权限，不存在失败风险。"""

    platform = PLATFORM_LINUX
    name = "procfs"

    def __init__(self):
        self.page = os.sysconf("SC_PAGE_SIZE")
        with open("/proc/stat", "r", encoding="ascii", errors="ignore") as fh:
            fh.readline()                    # 自检：文件必须可读

    def cpu_ticks(self):
        """返回 (忙碌 tick, 总 tick)，单位是内核时钟节拍"""
        try:
            with open("/proc/stat", "r", encoding="ascii", errors="ignore") as fh:
                vals = [int(x) for x in fh.readline().split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
            return sum(vals) - idle, sum(vals)
        except Exception:
            return None

    def memory(self):
        """返回 (已用字节, 总字节)；优先用 MemAvailable（比 MemFree 更接近真实可用）"""
        try:
            info = {}
            with open("/proc/meminfo", "r", encoding="ascii", errors="ignore") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    info[key.strip()] = int(rest.split()[0]) * 1024
            total = info.get("MemTotal")
            avail = info.get("MemAvailable", info.get("MemFree"))
            if not total or avail is None:
                return None
            return total - avail, total
        except Exception:
            return None

    def self_rss(self):
        """读 /proc/self/statm 第 2 个字段（常驻页数）"""
        try:
            with open("/proc/self/statm", "r", encoding="ascii") as fh:
                return int(fh.read().split()[1]) * self.page
        except Exception:
            return None


class _MacResReader:
    """
    macOS：ctypes 调 mach 内核接口（等价于活动监视器/ vm_stat 的数据源）。

      CPU   host_processor_info(PROCESSOR_CPU_LOAD_INFO) → 每核 user/system/idle/nice 累计 tick
      内存  host_statistics64(HOST_VM_INFO64)            → vm_statistics64（页数）
      自身  task_info(MACH_TASK_BASIC_INFO)              → resident_size
    """

    platform = PLATFORM_MACOS
    name = "mach API"

    # mach 常量
    _PROCESSOR_CPU_LOAD_INFO = 2
    _HOST_VM_INFO64 = 4
    _MACH_TASK_BASIC_INFO = 20

    class _VmStat64(ctypes.Structure):
        """对应 vm_statistics64_data_t（字段顺序必须与内核头文件一致）"""
        _fields_ = [
            ("free_count", ctypes.c_uint32), ("active_count", ctypes.c_uint32),
            ("inactive_count", ctypes.c_uint32), ("wire_count", ctypes.c_uint32),
            ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
            ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64),
            ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
            ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64),
            ("purges", ctypes.c_uint64), ("purgeable_count", ctypes.c_uint32),
            ("speculative_count", ctypes.c_uint32), ("decompressions", ctypes.c_uint64),
            ("compressions", ctypes.c_uint64), ("swapins", ctypes.c_uint64),
            ("swapouts", ctypes.c_uint64), ("compressor_page_count", ctypes.c_uint32),
            ("throttled_count", ctypes.c_uint32), ("external_page_count", ctypes.c_uint32),
            ("internal_page_count", ctypes.c_uint32),
            ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
        ]

    class _TaskBasicInfo(ctypes.Structure):
        """对应 mach_task_basic_info_data_t"""
        _fields_ = [
            ("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
            ("resident_size_max", ctypes.c_uint64),
            ("user_time", ctypes.c_int32 * 2), ("system_time", ctypes.c_int32 * 2),
            ("policy", ctypes.c_int32), ("suspend_count", ctypes.c_int32),
        ]

    def __init__(self):
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libc.mach_host_self.restype = ctypes.c_uint32
        libc.mach_task_self.restype = ctypes.c_uint32

        # host_processor_info(host, flavor, &cpu_count, &info_array, &info_count)
        libc.host_processor_info.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_uint32)]
        libc.host_processor_info.restype = ctypes.c_int
        # host_statistics64(host, flavor, &info, &info_count)
        libc.host_statistics64.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(self._VmStat64),
            ctypes.POINTER(ctypes.c_uint32)]
        libc.host_statistics64.restype = ctypes.c_int
        # task_info(task, flavor, &info, &info_count)
        libc.task_info.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(self._TaskBasicInfo),
            ctypes.POINTER(ctypes.c_uint32)]
        libc.task_info.restype = ctypes.c_int
        # vm_deallocate(task, address, size) —— 释放内核返回的 CPU tick 数组，防内存泄漏
        libc.vm_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        libc.vm_deallocate.restype = ctypes.c_int

        self.libc = libc
        self.host = libc.mach_host_self()
        self.task = libc.mach_task_self()
        self.page = os.sysconf("SC_PAGE_SIZE")
        self.total = os.sysconf("SC_PHYS_PAGES") * self.page

        # 自检：拿不到数据说明该平台/该 Python 不支持，交给上层回退
        if self.cpu_ticks() is None or self.memory() is None:
            raise RuntimeError("mach API 不可用")

    def cpu_ticks(self):
        """所有核心的 (忙碌 tick, 总 tick) 之和"""
        try:
            count = ctypes.c_uint32(0)
            info = ctypes.POINTER(ctypes.c_int)()
            info_cnt = ctypes.c_uint32(0)
            kr = self.libc.host_processor_info(
                self.host, self._PROCESSOR_CPU_LOAD_INFO,
                ctypes.byref(count), ctypes.byref(info), ctypes.byref(info_cnt))
            if kr != 0 or not info_cnt.value:
                return None
            vals = [info[i] for i in range(info_cnt.value)]
            addr = ctypes.cast(info, ctypes.c_void_p).value
            self.libc.vm_deallocate(self.task, ctypes.c_void_p(addr),
                                    ctypes.c_uint32(info_cnt.value * ctypes.sizeof(ctypes.c_int)))
            busy = total = 0
            for i in range(count.value):
                user, system, idle, nice = vals[i * 4:i * 4 + 4]
                busy += user + system + nice
                total += user + system + nice + idle
            return busy, total
        except Exception:
            return None

    def memory(self):
        """已用内存口径对齐「活动监视器」：匿名页-可回收 + wired + 压缩页"""
        try:
            st = self._VmStat64()
            cnt = ctypes.c_uint32(ctypes.sizeof(self._VmStat64) // ctypes.sizeof(ctypes.c_int))
            if self.libc.host_statistics64(self.host, self._HOST_VM_INFO64,
                                           ctypes.byref(st), ctypes.byref(cnt)) != 0:
                return None
            pages = (st.internal_page_count - st.purgeable_count
                     + st.wire_count + st.compressor_page_count)
            return pages * self.page, self.total
        except Exception:
            return None

    def self_rss(self):
        try:
            ti = self._TaskBasicInfo()
            cnt = ctypes.c_uint32(ctypes.sizeof(self._TaskBasicInfo) // ctypes.sizeof(ctypes.c_int))
            if self.libc.task_info(self.task, self._MACH_TASK_BASIC_INFO,
                                   ctypes.byref(ti), ctypes.byref(cnt)) != 0:
                return None
            return int(ti.resident_size)
        except Exception:
            return None


class _WindowsResReader:
    """Windows：kernel32 取 CPU 与内存，psapi 取本进程常驻内存"""

    platform = PLATFORM_WINDOWS
    name = "Win32 API"

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def __init__(self):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self.curr = self.k32.GetCurrentProcess()
        if self.memory() is None:
            raise RuntimeError("Win32 内存接口不可用")

    def cpu_ticks(self):
        """GetSystemTimes 返回的都是累计值（内核时间已含空闲时间，故忙碌 = 内核+用户-空闲）"""
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                            ("dwHighDateTime", ctypes.c_uint32)]

            idle, kern, user = FILETIME(), FILETIME(), FILETIME()
            if not self.k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                                           ctypes.byref(user)):
                return None

            def val(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            i, k, u = val(idle), val(kern), val(user)
            return (k + u - i), (k + u)
        except Exception:
            return None

    def memory(self):
        try:
            st = self._MemoryStatusEx()
            st.dwLength = ctypes.sizeof(self._MemoryStatusEx)
            if not self.k32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullTotalPhys - st.ullAvailPhys, st.ullTotalPhys
        except Exception:
            return None

    def self_rss(self):
        """本进程常驻内存（WorkingSetSize）"""
        try:
            pmc = self._ProcessMemoryCounters()
            pmc.cb = ctypes.sizeof(self._ProcessMemoryCounters)
            if not self.psapi.GetProcessMemoryInfo(self.curr, ctypes.byref(pmc), pmc.cb):
                return None
            return int(pmc.WorkingSetSize)
        except Exception:
            return None


class _FallbackResReader:
    """
    兜底：拿不到系统接口时使用。
    CPU 与内存均不提供，界面显示缺失标记。
    """

    platform = None
    name = "无可用系统接口"

    def cpu_ticks(self):
        return None

    def memory(self):
        return None

    def self_rss(self):
        return None


class SystemMonitor:
    """
    系统资源采样器（界面每秒调用一次）。

    平台实现由各 Reader 的 `platform` 字段决定；构造失败或采样失败都只降级显示，
    不影响主流程。CPU 使用率需要两次采样算差值，因此第一秒会显示 "—"。
    """

    def __init__(self):
        self._reader = None
        for cls in (_MacResReader, _WindowsResReader, _LinuxResReader):
            if cls.platform != PLATFORM:
                continue                     # 只实例化当前操作系统的实现
            try:
                self._reader = cls()
            except Exception:
                self._reader = None
        if self._reader is None:
            self._reader = _FallbackResReader()

        self.source = self._reader.name      # 界面上标明数据来源，便于排查
        self._prev = None                    # 上一次 CPU tick 快照

    def sample(self):
        """
        采样一次，返回 {"cpu", "mem_used", "mem_total", "self_rss"}。
        取不到的项为 None（界面显示 "—"）；本函数保证不抛异常。
        """
        out = {"cpu": None, "mem_used": None, "mem_total": None, "self_rss": None}

        try:
            cur = self._reader.cpu_ticks()
            if cur and self._prev and cur[1] > self._prev[1]:
                delta_busy = cur[0] - self._prev[0]
                delta_all = cur[1] - self._prev[1]
                out["cpu"] = max(0.0, min(100.0, 100.0 * delta_busy / delta_all))
            self._prev = cur                  # 首次采样只记快照，下一次才有差值
        except Exception:
            self._prev = None

        try:
            mem = self._reader.memory()
            if mem:
                out["mem_used"], out["mem_total"] = mem
        except Exception:
            pass

        try:
            out["self_rss"] = self._reader.self_rss()
        except Exception:
            pass

        return out
