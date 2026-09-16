# AGENTS.md

给 AI 编码助手（Codex / Claude Code / WorkBuddy 等）的项目须知。**动手前先读完本文**，尤其是「架构不变量」与「易踩坑点」两节——那里记录的每一条都是踩过的坑。

---

## 1. 项目概览

本仓库有两个**互不依赖**的组件，改其中一个不需要管另一个：

| 组件 | 入口 | 技术栈 | 启动/加载方式 |
|---|---|---|---|
| 抖音批量下载器 | `manifest.json` | Chrome MV3 + 原生 JS（无框架、无构建） | `chrome://extensions/` → 开发者模式 → 加载已解压的扩展 |
| 视频批处理工具 | `video_frame_tool.py` | Python 3.8+ 标准库 + Tkinter + ffmpeg | `python3 video_frame_tool.py` |

两个组件都是**单文件直改即生效**的形态：扩展没有打包步骤，Python 工具没有依赖安装步骤。**不要引入构建工具、打包器、npm 依赖或第三方 Python 包**（`imageio-ffmpeg` 是唯一例外，且仅在找不到系统 ffmpeg 时作为兜底被动态导入）。

## 2. 开发与自检命令

改完代码后按组件跑对应检查。CI（`.github/workflows/ci.yml`）跑的就是这一套：

```bash
# 扩展：语法 + 清单校验
node --check background.js
node --check content.js
node --check injected.js
node --check panel.js
node -e "JSON.parse(require('fs').readFileSync('manifest.json','utf8'))"

# Python 工具：编译检查（不会真正启动 GUI）
python3 -m py_compile video_frame_tool.py
```

功能验证建议（两个组件都没有自动化测试框架，只能手工/脚本验证）：

- **扩展**：改完在 `chrome://extensions/` 点刷新 → 打开 `douyin.com` 播一个视频 → 点图标出面板 → 勾选下载。看 console 有无报错，比对下载文件是否完整可播放。
- **Python 工具**：用 `ffmpeg -f lavfi -i color=...` 造几条测试素材（不同分辨率、含/不含音轨、时长各异），跑完用 `ffprobe` 校验输出**帧数、时长、分辨率**，并用 `ffmpeg -vf select=eq(n\,N)` 抽帧做**像素级抽检**（背景色、产品图位置、画中画出界与否）。这是本项目验证的既定手法，改滤镜链后务必照做。

## 3. 架构不变量（不要破坏）

### 3.1 扩展

1. **抓取与下载必须分在两个世界。**
   `injected.js` 跑主世界读 `window.player`（隔离世界读不到页面 JS 变量）；下载只在 `content.js`（隔离世界有 `host_permissions`，`fetch` 不受跨域限制）。**不要把 `fetch`/下载逻辑搬进 `injected.js`，也不要把读播放器的逻辑搬进 `content.js`。**
2. **面板只做界面，不做下载。**
   `panel.js` 与父页面跨源，只能通过 `postMessage` 收发指令；下载、去重、并发全在 `content.js`。
3. **关闭面板 = `display:none`，不销毁。**
   隐藏面板不能中断正在进行的下载。
4. **权限保持最小。**
   当前 `permissions` 只有 `scripting`。下载用「`fetch` → `Blob` → `<a download>`」实现，**不要为了下载而申请 `downloads` 权限**，也不要申请 `storage`（已下载记录用页面 `localStorage`）。
5. **消息协议改动必须双向同步。**
   新增 message type 时，`content.js` 与 `panel.js` 的 sender / receiver 一起改，并更新 `README.md` 的协议表。
6. **注入脚本要幂等。**
   两个脚本都用 `window.__DY_*__` 守卫防止重复注入（扩展重载、SPA 跳转会触发重复注入），新增入口时保持这个模式。

### 3.2 Python 工具

1. **零硬编码路径。**
   代码里**不允许出现任何具体用户路径**（如 `/Users/xxx/...`、`C:\Users\...`）。所有路径来自用户选择，并持久化到 `settings.json`。新增可配置项请走 `DEFAULT_SETTINGS` 结构 + `load_settings()` / `save_settings()`。
2. **平台差异只允许写在文件开头的「零、平台适配层」。**
   其它任何位置出现 `if sys.platform == ...` 或 `os.name == "nt"` 都算违规。平台函数**必须支持显式传 `platform=` 参数**，以便在不换系统的前提下验证三个分支。
3. **性能约束是硬要求**（详见 `video_frame_tool.py` 模块 docstring 与 README「性能设计」）：
   - 素材池探测结果必须走缓存（内存 + `.pip_cache.json` + 目录指纹），不得每次重新逐个 `ffprobe`；记录结构变化时同步升 `_POOL_CACHE_V`；
   - 画中画素材必须优先用 `prepare_small_pool()` 生成的加速副本，不要直接拿原素材去编码（理由见 README 性能实测）；
   - 并发处理时必须给每个 ffmpeg 分配线程配额 `max(1, CPU核数 // 并发数)`，不得放任 N 个编码器抢满核心；
   - 单次成片只允许编码两遍（画中画片段一次 + 最终合成一次），不要把首帧替换/产品图/画中画拆成多次全量重编码；
   - 日志必须限流（控件保留 1500 行 + 批量插入），不得在循环里逐条刷新 Tk 控件。
4. **画中画必须全程静音。**
   素材轨统一 `-an`。最终输出只保留主视频一条音轨。
5. **成片时长必须锁死为主视频时长。**
   画中画轨自行拼接后截断/补齐，最终输出再加 `-t` 兜底，避免因片段舍入导致时长漂移。
6. **首图 / 主图是「目录 + 每条视频随机取一张」。**
   `opts["cover"]` / `opts["product"]` 存的是**目录**（同时兼容只给单个文件的老写法），解析入口统一用 `list_images()`。
   每条视频在 `process_one()` 里通过 `_pick_images()` 随机固定一对，**同一条视频内不得再换图**（中途换图观众能看出来）。
   `_pick_images()` 返回**新 dict**，绝不能就地改多线程共享的 opts——这一点与 `_clamp_prod_start()` 同理。

## 4. 易踩坑点（血泪教训，改代码前先看）

| 坑 | 说明 |
|---|---|
| **`performance.memory` 是 getter，每次访问返回新快照** | 把它存进变量后在轮询里反复读属性，读到的永远是那一瞬间的冻结值，内存释放了也判不出来。必须**每次重新访问** `performance.memory` |
| **`blob.arrayBuffer()` 会复制整份数据** | 音轨检测只允许对 `blob.slice(0, 2MB)` 调用，直接对整个 Blob 调用等于每路并发再多占一份视频大小的内存 |
| **worker 错峰启动的时间不能太长** | 实测 10 线程 × 80 ms 错峰，跨度 720 ms，遇到 300 ms 就能返回的小文件时并发峰值只有 8。当前 40 ms 是刻意压下来的，调大前先算跨度 |
| **`setpts` 必须放在滤镜链里** | 加速用 `setpts=PTS/倍数`，它属于视频滤镜，要拼进 `-vf`，不要当成独立参数写在命令行上 |
| **并发任务重复扫描素材池** | 已用「进程内缓存 + 锁」解决；新增扫描入口时必须复用 `scan_pip_pool()`，不要在别处再写一份探测逻辑 |
| **Tk 关闭时序** | `after()` 定时器要在 `WM_DELETE_WINDOW` 里 `after_cancel`，否则关窗时报 `invalid command name ..._poll_queue` |
| **画中画源尺寸/比例与目标不一致** | 必须显式处理：`force_original_aspect_ratio` + 裁剪填满或加黑边，并按偶数对齐（h264 要求宽高为偶数）。实测踩坑：等比放大到"覆盖目标"后忘了 `crop`，3840×2160 素材填 258×384 得 683×384 奇数宽，x264 直接 `Invalid argument`，整条视频丢掉画中画。**crop_fill 分支必须无条件补 `crop=W:H`**，不要只在 `zoom > 1` 时补 |
| **别把「批量编码」当提速卖点** | 实测 400 秒成片 133 段：进程数 133 → 17，墙钟时间持平（64.8s vs 65.7s）——该阶段瓶颈在素材解码。批处理的收益是进程数少一个数量级、CPU 峰值平稳。提速大头是线程配额。改这条前先跑基准，别凭直觉写「性能提升 N 倍」 |
| **别指望「关掉某个滤镜」来提速** | 逐项消融实测（48 段 / 3 并发）：完整滤镜链 13.0s；**全部滤镜关掉只留缩放 13.2s**；**只解码不编码 13.1s**。翻转 / 调色 / 裁剪 / 加速 / 编码的成本全在噪声里，耗时 100% 花在「解码素材」上。想提速只能减少解码量——砍功能既没用，又会丢掉抗查重能力 |
| **素材加速副本是本项目最大的提速项** | `prepare_small_pool()` 把素材降成短边 480 的副本再参与拼接：画中画阶段 13~14s → **1.0s**，内存峰值 6.2 GB → **0.84 GB**。改画中画流程时不要绕过它。副本放 `<素材目录>/.pip_small/`，参数见 `PIP_SMALL_*` 常量 |
| **副本转码必须限制解码侧** | 不限制的话每个 ffmpeg 的解码器都默认吃满所有核心，几个并发就是几十个线程互踩：实测吞吐从 6.6 个/秒掉到 1.6 个/秒。用 `-threads 1`（非 macOS）或 `-hwaccel videotoolbox`（macOS 硬件解码，7.5 个/秒） |
| **ffmpeg 靠扩展名推断输出封装格式** | 临时文件写成 `xxx.mp4.part` 会直接报 `Unable to choose an output format`，402 个副本全部秒失败——而且失败被 `_run_quiet` 吞掉 stderr，极难察觉（表现为「2 秒跑完 402 个」，明显不合理）。临时文件名必须仍以真实扩展名结尾 |
| **手机竖屏素材是「存储横屏 + 旋转元数据」** | `ffprobe` 的 `width/height` 是**存储**尺寸，而 ffmpeg 解码时会按 `rotation` 摆正。本机素材池 **229/402** 属于这种情况，不换算会让横竖屏判断反过来、副本尺寸算错。`probe_dur_size()` 已统一返回**显示**尺寸，不要绕开它自己读宽高 |
| **素材池缓存记录加字段要升版本号** | 记录结构变化（v2 加宽高、v3 加旋转换算）时把 `_POOL_CACHE_V` +1，老缓存会自动失效重扫；否则下游读到缺字段的旧记录会静默出错 |
| **Tk 数值输入框的 `get()` 会抛 TclError** | 输入框被清空或填了非数字时，`IntVar.get()` / `tk.Spinbox` 关联变量的 `get()` 直接抛 `TclError: expected floating-point number but got ""`。`_snapshot()` 在**关窗时**也会被调用，一旦抛出就**关不掉窗口**。所有读输入框的地方都必须 `try/except` 兜住（见 `_snapshot` / `_num` / `_collect_opts`） |
| **输出路径不能等于源路径** | 输出目录若恰好等于源视频所在目录，`<base>.mp4` 会撞上源文件，ffmpeg 报 `Output ... same as Input #0 - exiting` 直接失败。`process_one()` 里已有同名检测并自动加 `_已处理` 后缀，改命名逻辑时别删 |
| **concat demuxer 的 list 文件不能写 Windows 反斜杠** | `-f concat` 把反斜杠当转义符，`C:\...\b000.mp4` 会被解析坏（报 No such file）。写 list 时必须 `path.replace("\\", "/")`，ffmpeg 在 Windows 上也接受正斜杠 |
| **macOS 自带 python3 的 Tk 是 8.5，读不了 PNG** | 实测 `/usr/bin/python3`(3.9.6/Tk 8.5)：`PhotoImage(data=png_b64)`、base64 PPM 全部报 `couldn't recognize image data`，**Tk 8.5 只认 PPM 文件**。所以 `load_logo()` 有三级回退：自定义 logo.png → 内嵌 PNG → 纯标准库解 PNG 转 PPM 临时文件。删掉 `_png_decode` / `_png_to_ppm_file` 会让 macOS 用户看不到 logo（现象是"改了但没变化"），改动前先用 `/usr/bin/python3` 验证一次 |
| **确认用到的是哪个解释器** | 本机 `python3` 可能指向托管 Python（Tk 9.0），也可能指向 `/usr/bin/python3`（Tk 8.5），两者 logo 加载路径完全不同。跨版本验证命令见 README「运行状态监控」小节 |
| **`.DS_Store` / `__pycache__` / `.workbuddy/` 不要提交** | 已在 `.gitignore` 排除。`.workbuddy/` 存本机记忆与原始插件备份，含本地绝对路径，**保留在本地但永不入库** |

## 5. 代码风格约定

- **注释与文档用中文**，与现有代码保持一致；每个文件顶部有一段说明职责的块注释，每个函数有 docstring（说明参数、返回、副作用）。
- **命名**：JS 用 `camelCase` 函数 / `SCREAMING_SNAKE_CASE` 常量；Python 用 `snake_case` / `SCREAMING_SNAKE_CASE`，模块内 `_private` 前缀表示内部函数。
- **不要引入 emoji 到代码与文档**（扩展日志前缀 `[抖晓晓]` 是既有文案，保留）。
- **注释解释「为什么」，不复述「做了什么」**；涉及性能与时序的数字（阈值、间隔、上限）要在注释里写清来源或实测依据。
- 改动滤镜链 / 并发策略 / 缓存策略时，**同步更新**：`video_frame_tool.py` 顶部 docstring、`README.md` 对应小节、本文件的相关条目。三处不一致即视为未完成。

## 6. 提交规范

- 分支：`main` 为默认分支。
- commit message 用中文，格式建议 `类型: 简述`，类型取 `feat` / `fix` / `perf` / `docs` / `refactor` / `chore`。
  例：`perf: 素材池加进程内缓存，并发时只扫描一次`
- 涉及性能的改动，在 message 正文附上实测前后对比数据。
- 一次提交只做一件事；格式化改动与功能改动分开。

## 7. 已知待办（未经确认不要擅自处理）

1. **命名不统一**：`manifest.json` 的 `name` 是「抖抖抖 抖音视频下载器 (批量下载)」，而运行时日志与面板标题用「抖晓晓」。统一命名会改变用户在 Chrome 扩展页看到的名字，属于产品决策，**需先与维护者确认**。
2. **`manifest.json` 的 `description` 仍是早期情绪化文案**，若要上架 Chrome 商店需重写。
3. **两个组件目前没有自动化测试**：只靠 CI 语法检查与手工验证。若要补，优先补 Python 工具的滤镜链像素级校验（最容易回归）。
4. **仓库尚未声明开源许可证**：在维护者决定之前不要添加 `LICENSE` 文件。
