"""一、默认参数与常量。

界面默认值、编码档位、画中画规格、低清副本规格、素材扩展名都在这里。
改动前先读项目 README 的「设计约束」——尤其是 PIP_SMALL_V / _POOL_CACHE_V 的升版规则。"""

import os

# ============================================================================
# 一、默认参数与常量
# ============================================================================
# 说明：这里不预设任何素材路径。程序会把用户"上次选择过的路径"记录在
#       settings.json 里（位置见"零、平台适配层"的 user_config_dir），
#       下次启动自动回填；首次运行需要用户手动选择一次。
# ============================================================================

# ---- 产品图叠加 ----
MARGIN_BOTTOM = 20            # 产品图距视频底部的像素距离
PRODUCT_WIDTH_RATIO = 0.25    # 产品图宽度 = 视频宽度 * 该比例
PRODUCT_START_FRAME = 60      # 产品图从第几帧开始显示（默认第 60 帧；填 0 表示首帧就显示）
PRODUCT_START_MAX = 100000    # 起始帧输入框的防御性上限
# 商品图显示概率（%）：每条视频**独立**掷一次骰子，不是"整批抽 N 条"。
#   0   → 完全不叠加商品图（这条只剩首图 + 画中画）
#   100 → 每条视频都从主图目录里随机取一张叠加
#   40  → 40% 的概率叠加，60% 的概率这条不叠
# 概率为 0 或掷骰未命中时，是**真的不做这道工序**（省掉一路输入和一段 overlay），
# 而不是叠一张透明图糊弄过去（见 _decide_product / build_command）。
PRODUCT_CHANCE = 100

# ---- 画中画默认几何（比例均相对主视频宽/高，对应参考截图红框） ----
PIP_W_RATIO = 0.24            # 宽 24%
PIP_H_RATIO = 0.20            # 高 20%
PIP_RIGHT_RATIO = 0.08        # 右边缘距视频右侧 8%
PIP_TOP_RATIO = 0.09          # 上边缘距视频顶部 9%
PIP_FILL = "裁剪填满"          # 内容填充方式：裁剪填满 / 完整显示

# ---- 画中画素材处理默认值 ----
PIP_HEAD_TRIM = 10.0          # 默认掐掉素材前 10% 的时长
PIP_TAIL_TRIM = 10.0          # 默认掐掉素材后 10% 的时长
PIP_SPEED = 1.2               # 默认加速倍数
# 单个片段的随机时长范围（秒）。
# 性能相关：段数 ≈ 主视频时长 / 平均段长，段数直接决定画中画阶段的进程数与总耗时。
# 3~8 秒会产生过多片段（9 分钟视频约 100 段），故放宽到 4~12 秒（段数约减半）。
SEG_MIN, SEG_MAX = 4.0, 12.0
MAX_SEGMENTS = 600            # 单个成片最多拼接的片段数（防御性上限，防死循环）

# ---- 画中画编码的批处理参数（性能关键，改动前请看"性能设计"） ----
# 早期实现是"一段一个 ffmpeg 进程、串行执行"，9 分钟视频要启动 100+ 次进程；
# 现在改为把 PIP_BATCH 个片段塞进**同一个 ffmpeg 进程**（多输入 + concat 滤镜）一次编完，
# 进程数从 100+ 降到个位数，且批次之间并行执行。
PIP_BATCH = 8                 # 每批片段数（= 单个 ffmpeg 进程的输入路数）
PIP_BATCH_PRESET = "ultrafast"  # 批次编码档位：画面小（约 260x384）+ 后面还会被重编码一次
PIP_BATCH_THREADS = 2         # 单批次编码线程数（批内已有多路输入并行解码，不宜再吃多线程）

# ---- 画中画素材的"低清副本"（实测最大的提速项，改动前请看 README 的性能实测） ----
# 画中画在成片里最终只占约 260x384 像素，而素材原片常见 1080p/4K。
# 逐项消融实测（48 段 / 3 并发）：
#     现状                       13~14 秒，内存峰值 6.2 GB
#     加硬件解码                  8.1 秒（-42%）
#     素材先降成小副本再拼接       1.0 秒（-93%），内存峰值 0.84 GB（-86%）
# 原因：图像滤镜、调色、翻转、加速、甚至编码本身的开销都约等于 0，
#       全部时间都花在"解码 1080p 素材"上。把素材预先变小，解码量直接砍掉一个数量级，
#       而观感毫无差别——因为素材最终也只显示那么大。
# 副本**只生成一次，之后永久保留**（不会在任务结束后被清理）：
# 每次开始处理前先按台账核对一遍，已经生成过的直接拿来用，一个字节都不重做。
# 副本目录放在素材目录内，不会被当成素材扫进来（_list_videos 不递归）。
PIP_SMALL_DIR = ".pip_small"    # 副本存放目录名（位于素材目录内）
PIP_SMALL_THREADS = 4           # 并发生成副本的线程数（6 个并发只快 10%，但内存多 1.5 倍）
PIP_SMALL_CRF = 20              # 副本画质（20 接近视觉无损，且副本后面还会被重编码）
PIP_SMALL_GOP = 15              # 副本关键帧间隔（越小定位越准、越快）
PIP_SMALL_MIN_SHORT = 480       # 副本短边下限（保证够画中画目标尺寸用）
PIP_SMALL_HEADROOM = 1.15       # 相对画中画目标尺寸的额外安全余量
PIP_SMALL_PRESET = "veryfast"   # 副本编码档位（副本后面还要被重编码一次，不必更慢的档）
# 副本"规格版本"：任何会改变副本产出的改动（缩放策略、编码参数、尺寸算法）都要 +1，
# 否则用户会一直用着旧规格的副本。+1 后旧副本会在下次处理时自动重做。
PIP_SMALL_V = 1
PIP_SMALL_META = "_meta.json"   # 副本台账：记录每个副本对应哪个源文件、按什么参数生成
PIP_SMALL_ROSTER = "_说明.txt"  # 放进副本目录的说明（告诉用户这些文件是什么、能不能删）

# ---- 抗查重随机化默认值 ----
RND_FLIP_H = True             # 随机水平翻转（默认开）
RND_ZOOM = 1.06               # 随机缩放上限（1.0 = 关闭）
RND_COLOR = True              # 随机亮度/对比度/饱和度微扰
RND_JITTER = 3                # 画中画位置随机抖动像素（0 = 关闭）

# ---- 编码参数（preset 与 CRF 是一组，改之前先看实测表） ----
# 成片的耗时几乎全在编码：实测"只解码不编码"0.34s，而完整命令 6.7s —— 滤镜、overlay、
# 缩放、素材解码全在噪声里（见 README「性能实测」）。所以**唯一有效的提速手段是降低
# 编码器工作量**，而 preset 就是那个总开关。
# 实测（1080p/20s 竖屏，同一条滤镜链，只换编码档位，3 轮取中位；5 并发下同向）：
#     veryfast  /CRF18   1.00x   27.8MB   SSIM 1.0000   ← 旧默认
#     superfast /CRF18   1.43x   37.2MB   SSIM 0.9889
#     superfast /CRF20   1.57x   28.6MB   SSIM 0.9870   ← 现默认（提速且体积持平）
#     superfast /CRF21   1.65x   25.0MB   SSIM 0.9858
#     ultrafast /CRF24   2.59x   51.0MB   SSIM 0.9678   ← 体积近两倍，不采用
#     fast      /CRF18   0.35x   26.1MB   SSIM 0.9909   ← 比 veryfast 慢 2.9 倍
# 结论：preset 每降一档收益约 40%，而 CRF 同时 +2 可把体积找回来 —— 所以默认档位
# 定为 superfast/CRF20。注意**不要为了提速换硬件编码**：苹果媒体引擎是共享资源，
# 单条 2.27x，5 并发只剩 1.06x（详见 README「硬件加速实测」）。
CRF = 20                      # x264 质量；与 preset 配套（20 配 superfast，体积≈旧的 veryfast/18）
PRESET_MAP = {                # 界面下拉 → ffmpeg preset
    # 旧配置里存的"快速/均衡/高质量"三个名字保持不变，映射整体上移一档：
    # 老用户重新打开就自动变快，不需要迁移配置。
    "快速": "superfast",      # 默认档，实测 1.57x
    "均衡": "veryfast",       # 原为 fast（实测只有 0.35x，名不副实）
    "高质量": "fast",         # 原为 medium
    "最高质量": "medium",      # 压得最狠，慢，仅在需要极限压缩率时用
}
DEFAULT_PRESET = "快速"
DEFAULT_WORKERS = 5           # 默认并发线程数
MONITOR_INTERVAL_MS = 1000    # 系统资源监控的刷新间隔（毫秒）

# ---- 硬件加速（界面「硬件加速」下拉）----
# 只加速"解码"。为什么不给硬件编码：实测硬编更慢（见 hw_decode_args 里的数据），
# 因为这条流水线里编码几乎不花时间，换硬编只会多出把画面搬进搬出 GPU 的开销。
HWACCEL_AUTO = "自动"          # macOS 自动开硬解；其它平台保持现状（软件解码）
HWACCEL_ON = "开启"            # 强制启用硬解（非 macOS 交给 ffmpeg 自选 cuda/qsv/vaapi）
HWACCEL_OFF = "关闭"           # 一律软件解码（排查花屏 / 兼容性问题时用）
HWACCEL_MODES = (HWACCEL_AUTO, HWACCEL_ON, HWACCEL_OFF)
DEFAULT_HWACCEL = HWACCEL_AUTO
# 硬解用在哪几个阶段：只有"要解码 1080p/4K 原片"的阶段才真快，
# 其余阶段实测没收益甚至更慢，所以"自动"只在这些阶段开（见 hw_decode_args 实测表）
HW_STAGE_COPY = "copy"          # 生成低清副本：解码 1080p/4K 原片
HW_STAGE_PIP_RAW = "pip_raw"    # 画中画片段编码，输入是原片
HW_STAGE_PIP_SMALL = "pip_small"  # 画中画片段编码，输入是 480p 副本
HW_STAGE_MAIN = "main"          # 主合成：解码主视频
HW_AUTO_STAGES = (HW_STAGE_COPY, HW_STAGE_PIP_RAW)

# ---- 其它 ----
CPU_COUNT = os.cpu_count() or 4
LOG_MAX_LINES = 20          # 日志控件保留的最大行数，超出丢弃最老的
SCAN_THREADS = 16             # 素材扫描的并行探测线程数

# ---- 配置文件结构（默认值的唯一来源：上面的常量） ----
SETTINGS_VERSION = 1
DEFAULT_SETTINGS = {
    "version": SETTINGS_VERSION,
    "paths": {                          # 上次选择过的路径（核心：代码里不写死任何路径）
        "cover": "",                    # 首图目录（每条视频随机取一张；也兼容单个图片文件）
        "product": "",                  # 主图目录（同上）
        "video_dir": "",                # 待处理视频目录
        "pip_dir": "",                  # 画中画目录
    },
    "pip": {                            # 画中画参数（界面输入框用字符串保存）
        "enabled": True,                # 是否启用画中画（总开关；关掉后不看下面的参数）
        "w": str(int(PIP_W_RATIO * 100)),
        "h": str(int(PIP_H_RATIO * 100)),
        "right": str(int(PIP_RIGHT_RATIO * 100)),
        "top": str(int(PIP_TOP_RATIO * 100)),
        "fill": PIP_FILL,
        "head": f"{PIP_HEAD_TRIM:g}",
        "tail": f"{PIP_TAIL_TRIM:g}",
        "speed": f"{PIP_SPEED:g}",
        "small": True,                  # 是否为画中画素材建立低清加速副本（强烈建议开）
    },
    "random": {                         # 抗查重随机化开关
        "flip_h": RND_FLIP_H,
        "zoom": f"{RND_ZOOM:g}", "color": RND_COLOR, "jitter": str(RND_JITTER),
    },
    "run": {                            # 运行参数
        "workers": DEFAULT_WORKERS, "preset": DEFAULT_PRESET,
        "prod_start": PRODUCT_START_FRAME,   # 产品图起始帧（默认 60）
        "prod_chance": PRODUCT_CHANCE,       # 商品图显示概率（%，每条视频独立掷骰）
        "hwaccel": DEFAULT_HWACCEL,          # 硬件加速：自动 / 开启 / 关闭（见 hw_decode_args）
        "include_first": False,              # 旧字段，仅用于兼容老配置，见 load_settings
    },
}

VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".flv", ".wmv",
    ".webm", ".ts", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".rmvb",
}
KEEP_EXT = {".mp4", ".mov", ".m4v", ".mkv"}   # 可直接承载 h264 的容器，其余统一输出 .mp4

# 可当首图 / 主图用的图片后缀。
# 首图和主图都支持「给一个目录，每条视频从里面随机取一张」。
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".jfif"}
