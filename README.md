# video_downloader

抖音视频/图集批量下载器 + 短视频二次加工工具。

仓库包含两个彼此独立的组件：

| 组件 | 形态 | 作用 |
|---|---|---|
| **抖音批量下载器** | Chrome 扩展（Manifest V3） | 在抖音网页里抓取当前播放过的视频/图集，勾选后多线程批量下载，自动跳过已下载 |
| **视频批处理工具** | Python 桌面 GUI（Tkinter） | 把一批视频做二次加工：换首帧、贴产品图、右上角叠加随机拼接的画中画，输出到 `out/` |

---

## 目录结构

```
.
├── manifest.json           # 扩展清单（MV3）
├── background.js           # Service Worker：图标点击 → 找抖音标签页 → 显示面板
├── content.js              # 内容脚本（隔离世界）：列表去重、浮层面板、下载调度
├── injected.js             # 注入页面主世界：读取播放器数据并上报
├── panel.html/.css/.js     # iframe 面板界面（原生 JS，无框架）
├── images/                 # 扩展图标 16/32/128
├── video_frame_tool.py     # Python GUI 视频批处理工具（纯标准库 + ffmpeg）
├── logo.png                # Python 工具的品牌图（可选；删掉就用脚本内嵌的那份）
├── .github/workflows/ci.yml# CI：JS 语法 + manifest 校验 + Python 编译检查
├── AGENTS.md               # 给 AI 编码助手的项目须知（架构约束、易踩坑点）
└── README.md
```

---

# 一、抖音批量下载器（Chrome 扩展）

## 功能

- **自动识别**：在抖音网页里播放过的视频会自动进入面板列表，按 `shareUrl` 去重，最新的排在最前
- **图集支持**：自动识别图文帖，逐张下载原图
- **批量下载**：全选 / 反选 / 勾选任意条，多线程并发拉取
- **免重复下载**：下载成功的条目写入 `localStorage`，刷新页面后仍标记「已完成」，不再重复下
- **多候选地址回退**：抖音不同接口给出的直链音轨情况不一致，脚本会把 `playApi` / `playAddr` / `bitRateList` 全部收集，逐个尝试直到成功
- **无音轨补救**：若视频源本身不含音轨，额外保存一条配乐文件（`_配乐.m4a` / `_配乐.mp3`）
- **可拖拽浮层**：面板挂在 Shadow DOM + iframe 里，与页面样式完全隔离；支持拖动、折叠、隐藏

## 安装

1. 打开 Chrome，访问 `chrome://extensions/`
2. 右上角打开 **开发者模式**
3. 点 **加载已解压的扩展程序**，选择本仓库根目录
4. 无需构建步骤，改完代码在扩展页点一次「刷新」即可

> 要求 Chrome 102+（Manifest V3 + `chrome.scripting`）。Edge / 其它 Chromium 内核浏览器同理。

## 使用

1. 打开 `https://www.douyin.com/`，正常刷视频（**播放过的**视频才会被记录）
2. 点扩展图标 → 页面右上角出现浮层面板
3. 勾选需要的条目 → 点「批量下载」
4. 文件保存到**浏览器默认下载目录**

若面板没出现（扩展刚重载、页面在重载前就打开了），service worker 会自动补注入一次；仍失败就刷新抖音页面再点图标。

## 工作原理

```
抖音页面
 ├─ 主世界 (Main World)
 │   injected.js ──读 window.player.config.awemeInfo──▶ 整理成 media 对象
 │        │                                                  │
 │        │ window.postMessage                              │
 │        ▼                                                  │
 └─ 隔离世界 (Isolated World)
     content.js ──去重/建档──▶ mediaList ──▶ Shadow DOM 宿主
        │                                      └─ iframe: panel.html + panel.js
        │  fetch（受 host_permissions 保护，不受跨域限制）
        ▼
     Blob ──▶ <a download> ──▶ 浏览器下载目录
```

**为什么必须分两个世界**：`window.player` 是抖音页面自身的 JS 变量，隔离世界读不到，所以抓数据只能注入主世界；而 `fetch` 媒体直链需要绕过跨域，只能用隔离世界（扩展有 `host_permissions`），因此「抓」和「下」分开。

## 脚本职责

| 文件 | 运行环境 | 职责 |
|---|---|---|
| `background.js` | Service Worker | 只做「找人 + 传话」：定位抖音标签页 → 发 `show_panel`；必要时补注入 `content.js`；没有抖音页就打开首页 |
| `injected.js` | 页面主世界 | 轮询 `window.player`（每秒一次），挂钩 `play` / `seeked` / `loadeddata` 事件；把播放器数据规整成统一 media 对象后上报。**不含任何下载逻辑** |
| `content.js` | 页面隔离世界 | 注入 `injected.js`、维护去重列表、构建浮层面板、执行下载与并发调度 |
| `panel.js` | iframe 内 | 纯界面：渲染列表、收集勾选、显示进度；通过 `postMessage` 与 `content.js` 通信 |

## 消息协议

`content.js` ⇄ `panel.js`（跨源 iframe，走 `postMessage`）：

| 方向 | type | 说明 |
|---|---|---|
| content → panel | `media_list` / `panel_init` | 全量列表刷新 |
| content → panel | `item_status` | 单条状态与进度（0~100） |
| content → panel | `item_error` | 单条失败原因 |
| content → panel | `batch_started` / `batch_progress` / `batch_finished` | 批量生命周期 |
| content → panel | `toast` | 轻提示 |
| panel → content | `panel_ready` / `start_download` / `stop_download` | 面板就绪、开始、停止 |

`injected.js` ⇄ `content.js`（同窗口，`window.postMessage`，靠 `source` 字段区分）：`media_found`（上报）、`request_current_media`（补抓）。

## 并发策略：线程数交给浏览器决定

不是拍脑袋写死并发数，而是两步自适应（见 `content.js`）：

1. **开跑前估线程数**：`浏览器内存预算 × 70% ÷ 本次待下视频平均体积`，夹在 `2 ~ 16` 之间。预算优先级：`performance.memory.jsHeapSizeLimit` > `navigator.deviceMemory` 的一半 > 兜底常量（单条按 80 MB 估）。
2. **跑起来后看实时堆压力**：每个 worker 领新任务前检查堆占用比例，超过 85% 就暂停领活，回落到 60% 以下再继续（滞回设计，避免临界抖动），单次等待最长 20 秒兜底。

配套细节：worker 错峰启动（每个间隔 40 ms，避免同一毫秒齐发）；共享游标领任务，天然不重复；`fetch` 进度按 2% 步进回调，避免高频刷新界面。

## 已知限制与注意事项

- **只记录播放过的视频**：列表来自播放器实例，不滚动播放就不会进入列表
- **文件名重名**：同名文件由浏览器自动追加 `(1)`，扩展不做改名
- **音轨检测是启发式的**：只在文件开头 2 MB 内搜 `soun` / `mp4a` / `ac-3` 关键字，仅用于提示，失败不影响下载
- **`performance.memory` 是 Chrome 专有 API**：其它内核拿不到时会退回设备内存估算
- **停止是「下完当前这条」**：点停止后各 worker 不再领新任务，已开始的请求会跑完
- **命名待统一**：`manifest.json` 里的扩展名为「抖抖抖 抖音视频下载器 (批量下载)」，而运行时日志前缀与面板标题用的是「抖晓晓」，属历史遗留；`manifest.json` 的 `description` 字段仍是早期的一句情绪化文案，若要上架 Chrome 商店需要改写

---

# 二、视频批处理工具（Python GUI）

## 功能

四项能力，可任意组合：

| # | 功能 | 说明 |
|---|---|---|
| 1 | **首帧替换** | 用指定图片替换每个视频的第 1 帧 |
| 2 | **产品图叠加** | 从中下方叠加产品图：距底部 20 px、水平居中、宽度 = 视频宽度的 1/4；**从第几帧开始显示可设置（默认第 60 帧）** |
| 3 | **批量处理** | 扫描所选目录内的视频，多线程并发，输出到 `<视频目录>/out/`，线程数可设（默认 5） |
| 4 | **画中画（PIP）** | 从「小视频目录」随机抽取**互不重复**的片段拼成一条**静音**轨，叠在画面右上角，总长度自动对齐主视频 |

界面底部还有**实时资源监控**：CPU 使用率、内存占用、本工具自身内存占用，以及正在转码的任务数（每秒刷新一次）。

## 环境要求

- **Python 3.8+**（只用标准库：`tkinter` + `subprocess`，无需 pip 装包）
- **ffmpeg / ffprobe**：优先查 `PATH`，其次各平台常见安装目录，最后回退 pip 包 `imageio-ffmpeg` 自带二进制
  - macOS：`brew install ffmpeg`
  - Windows：`winget install ffmpeg` 或下载官方包解压后加入 `PATH`
  - Linux：`apt install ffmpeg` / `dnf install ffmpeg`

## 运行

```bash
python3 video_frame_tool.py
```

Linux 下若报 `No module named tkinter`：`sudo apt install python3-tk`。

## 界面参数

**路径（4 项，记住上次选择）**：首帧替换图、产品图、小视频目录、待处理视频目录。

**画中画几何**（比例均相对主视频宽/高）：

| 参数 | 默认 | 含义 |
|---|---|---|
| 宽 / 高 | 24% / 20% | 画中画尺寸 |
| 右距 / 上距 | 8% / 9% | 距视频右侧、顶部的距离 |
| 填充 | 裁剪填满 | 素材比例不同时：裁剪填满 / 完整显示（加黑边） |

**画中画素材处理**：

| 参数 | 默认 | 含义 |
|---|---|---|
| 掐头 / 去尾 | 各 10% | 丢弃素材首尾各 10% 的时长，避开片头片尾、水印、黑场；可分别设置 |
| 加速 | 1.2 倍 | 成片贡献时长 = 截取素材长度 ÷ 加速倍数 |
| 随机水平翻转 | 开 | 每段独立 50% 概率镜像 |

**产品图起始帧**：默认第 **60** 帧起显示（视频按 30fps 计约第 2 秒），填 `0` 表示从第一帧就显示。

**抗查重随机化**（每段独立随机，全部默认开启）：随机缩放上限 1.06、亮度/对比度/饱和度各 ±2% 微扰、画中画位置抖动 ±3 px，另有素材洗牌不重复、随机起点、随机 **4~12 秒**段长。

## 运行状态监控

窗口顶部是 logo 与标题，底部状态栏右侧实时显示：

| 显示项 | 说明 |
|---|---|
| CPU | 全系统 CPU 使用率（0~100%），带进度条 |
| 内存 | 系统已用/总内存。macOS 口径与「活动监视器」一致 = 匿名页−可回收 + wired + 压缩页 |
| 本工具占用 | 本进程常驻内存（RSS） |
| 正在转码 N 个 | 当前在跑的 ffmpeg 数量 |

每秒刷新一次，接口都是各平台原生廉价调用（macOS mach API / Linux procfs / Windows Win32 API），**不依赖 psutil 等第三方库**，也不用子进程。每台机器能取到什么就显示什么，取不到的项显示 `—`（例如首次采样还不足以算出 CPU 差值）。

界面顶部的 logo 内嵌在脚本里（base64 PNG），**不依赖任何外部图片文件**。想换成自己的品牌图：把 `logo.png` 放到 `video_frame_tool.py` 同目录或用户配置目录即可，程序会优先采用它。

## 输出

- 输出目录：`<所选视频目录>/out/`
- 编码：`libx264` / `crf 18` / `yuv420p` / `-movflags +faststart`
- 音频：源为 `aac`/`mp3` 直接 copy，其它格式转 `aac`；无音轨也能处理
- 容器：源为 `mp4`/`mov`/`m4v`/`mkv` 时沿用扩展名，其它统一输出 `.mp4`

## 配置记忆与跨平台适配

代码中**不写死任何素材路径**，用户上次选择的 4 个路径、画中画参数、随机化开关、并发数与编码档位全部记在 `settings.json`：

| 平台 | 配置文件位置 |
|---|---|
| Windows | `%APPDATA%\video_frame_tool\settings.json` |
| macOS | `~/Library/Application Support/video_frame_tool/settings.json` |
| Linux | `$XDG_CONFIG_HOME/video_frame_tool/settings.json`（默认 `~/.config/…`） |

所有系统差异集中在文件开头的「零、平台适配层」，其它代码只调用该层函数：

| 差异点 | Windows | macOS | Linux |
|---|---|---|---|
| 配置目录 | `%APPDATA%` | `~/Library/Application Support` | `$XDG_CONFIG_HOME` / `~/.config` |
| 打开输出目录 | `os.startfile` | `open` | `xdg-open` |
| 日志等宽字体 | Consolas | Menlo | DejaVu Sans Mono |
| ffmpeg 搜索 | Program Files / winget / scoop 等 | Homebrew 3 处 | `/usr/bin`、snap、flatpak |
| 可执行名 | `ffmpeg.exe` | `ffmpeg` | `ffmpeg` |
| 高 DPI | 设 DPI 感知 | 系统处理 | 系统处理 |

平台函数均支持显式传 `platform=` 参数，因此**不换系统也能验证三分支**（`video_frame_tool` 内 `detect_platform` / `user_config_dir` / `open_folder` / `mono_font_family` / `ffmpeg_search_dirs` / `exe_name`）。

## 性能设计（改代码时请保持这些约束）

1. **编码线程配额按「实际同时处理的视频数」分配**：每个 ffmpeg 拿到 `max(1, CPU核数 // 实际并发数)` 线程。**不能用界面上填的并发数**——只处理 2 个文件却填了 5 时，按 5 分配会让 2 个文件只用到 4/14 核的算力，白白慢好几倍。
2. **画中画片段批量编码**：每 `PIP_BATCH`（默认 8）个片段塞进**同一个 ffmpeg 进程**（多输入 + `concat` 滤镜）一次编完，批次之间并行。早期实现是"一段一个进程、串行跑"，9 分钟视频要启动 100+ 次 ffmpeg，进程启停开销比编码本身还大——这是之前慢和风扇狂转的主因。
3. **素材池元数据缓存**：400+ 素材逐个 `ffprobe` 很慢。首次扫描结果（时长 + 目录指纹）落盘到 `<素材目录>/.pip_cache.json`，再次启动校验指纹后复用；进程内还有一层内存缓存，多个视频并发时只有第一个真正扫描。（实测 402 个素材首次 1.1 秒，缓存命中 0.035 秒）
4. **素材扫描并行**：首次扫描用 16 路线程池并行探测。
5. **批次拼接用 stream copy**：各批参数完全一致（同尺寸/帧率/像素格式），直接 `-c copy` 拼接，不再解码重编码。
6. **单次成片只编码两遍**：画中画片段一次 + 最终合成一次（首帧/产品图/画中画全在同一条 `filter_complex` 里完成）。
7. **日志限流**：Text 控件只保留最后 1500 行，且每轮批量插入一次，避免高频刷新拖慢 Tk 主线程。
8. **尺寸归一化不能省**：片段缩放后一律 `crop` 回目标尺寸。等比放大到"覆盖目标"会算出奇数边（3840×2160 素材填 258×384 → 683×384），而 h264 要求宽高为偶数，会直接报错、让整个视频丢掉画中画。
9. **单段坏了不牵连整条**：某批编码失败时自动拆成单段逐个重试，只丢弃真正跑不通的素材，并用其它素材补足缺失时长。

**实测数据**（14 核 macOS，1080×1920 主视频 + 402 个真实素材）：

| 项目 | 实测 |
|---|---|
| 线程配额按实际并发数 | 60 秒片：5.3s → 3.2s（**1.69x**）；推算 9 分 04 秒片：48s → 29s |
| 素材池首次扫描 / 缓存命中 | 1.1s / 0.035s |
| 画中画批量编码（进程数） | 133 → 17，墙钟约 1.0x（见第 2 条口径说明） |
| 端到端 | 60 秒片 9.4s（推算 9 分 04 秒片约 1.4 分钟） |
| 4K HEVC 素材 | 修复前直接报错整条报废 → 修复后正常输出 |

## 抗查重设计（关键，改动前务必理解）

平台查重主要看画面哈希与镜头序列，因此工具在素材层做多重随机化：

| 维度 | 做法 |
|---|---|
| 素材选择 | 每轮洗牌，同一素材用尽前不重复 |
| 片段起点 | 在素材「有效区间」内随机取 |
| 片段长度 | 每段随机 4~12 秒（`SEG_MIN` / `SEG_MAX` 可调） |
| 掐头去尾 | 默认各丢弃 10%，避开片头片尾/水印/黑场 |
| 播放速度 | 默认 1.2 倍，帧序列整体变化 |
| 镜像 | 随机水平/垂直翻转 |
| 缩放 | 放大 1.00~1.06 倍后裁回固定尺寸，像素发生位移 |
| 调色 | 亮度/对比度/饱和度各 ±2% 微扰 |
| 位置 | 画中画位置 ±3 px 抖动 |

---

## 常见问题

**扩展面板不出现**
刷新抖音页面后重新点扩展图标。仍无效时到 `chrome://extensions/` 看 service worker 有无报错。

**下载失败 / 文件很小**
抖音直链有时效性，换一个视频地址重试即可；扩展本身已按候选地址逐个回退。失败的条目在面板里会标红并显示原因。

**视频没有声音**
该源本身只有视频轨，扩展会额外保存一条 `_配乐` 文件，需要用剪辑软件自行合轨。

**Python 工具提示找不到 ffmpeg**
按上文「环境要求」安装；或 `pip install imageio-ffmpeg` 作为兜底（工具会自动发现它自带的二进制）。

**Python 工具生成的 `.pip_cache.json` 是什么**
画中画素材池的元数据缓存（素材时长 + 文件指纹），位于素材目录内。素材增删改后指纹变化会自动重建，直接删除也无影响。

---

## 开发与贡献

- **无构建步骤**：扩展改完代码在 `chrome://extensions/` 点刷新；Python 工具直接跑脚本。
- **提交前自检**（CI 也在跑同样的检查）：
  ```bash
  node --check background.js && node --check content.js \
    && node --check injected.js && node --check panel.js
  node -e "JSON.parse(require('fs').readFileSync('manifest.json','utf8'))"
  python3 -m py_compile video_frame_tool.py
  ```
- 改动较大的功能建议在 commit message 里说明「改了什么 + 为什么」，涉及性能约束（见上文）的改动请附实测数据。
- 面向 AI 编码助手的项目约定、架构不变量与易踩坑点见 [AGENTS.md](AGENTS.md)。

## 免责声明

本仓库仅供个人学习与自有内容备份使用。请遵守抖音平台的用户协议与相关法律法规，**不得用于下载、传播侵权内容或任何商业用途**；因使用本工具产生的任何后果由使用者自行承担。视频加工功能涉及的功效性表述须遵守《广告法》等规定，本工具不提供任何内容合规担保。
