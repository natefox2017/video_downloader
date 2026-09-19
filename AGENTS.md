# AGENTS.md

给 AI 编码助手（Codex / Claude Code / WorkBuddy 等）的项目须知。**动手前先读完本文**，尤其是「架构不变量」与「易踩坑点」两节——那里记录的每一条都是踩过的坑。

---

## 1. 项目概览

本仓库有两个**互不依赖**的组件，改其中一个不需要管另一个：

| 组件 | 入口 | 技术栈 | 启动/加载方式 |
|---|---|---|---|
| 抖音批量下载器 | `manifest.json` | Chrome MV3 + 原生 JS（无框架、无构建） | `chrome://extensions/` → 开发者模式 → 加载已解压的扩展 |
| 视频批处理工具 | `video_frame_tool/`（src 布局包，包名 = `src`，入口 `__main__.py`） | Python 3.8+ 标准库 + Tkinter + ffmpeg | `cd video_frame_tool && ./run.sh`（一键启动，自动挑带 tkinter 的解释器；也可 `PYTHONPATH=. python3 -m src`） |

两个组件都是**直改即生效**的形态：扩展没有打包步骤，Python 工具没有依赖安装步骤
（原单文件 `video_frame_tool.py` 已按功能拆分到 `video_frame_tool/src/` 下）。
**不要引入构建工具、打包器、npm 依赖或第三方 Python 包**（`imageio-ffmpeg` 是唯一例外，且仅在找不到系统 ffmpeg 时作为兜底被动态导入）。

## 2. 开发与自检命令

改完代码后按组件跑对应检查。CI（`.github/workflows/ci.yml`）执行语法/编译检查；视频回归脚本需另外运行：

```bash
# 扩展：语法 + 清单校验
node --check background.js
node --check content.js
node --check injected.js
node --check panel.js
node -e "JSON.parse(require('fs').readFileSync('manifest.json','utf8'))"

# Python 工具：编译检查（不会真正启动 GUI）
python3 -m compileall -q video_frame_tool/src video_frame_tool/tests
# 视频回归：需要 ffmpeg/ffprobe，只使用临时素材
python3 video_frame_tool/tests/test_video_frame_tool.py
```

功能验证建议（两个组件都没有自动化测试框架，Python 已有标准库回归脚本）：

- **扩展**：改完在 `chrome://extensions/` 点刷新 → 打开 `douyin.com` 播一个视频 → 点图标出面板 → 勾选下载。看 console 有无报错，比对下载文件是否完整可播放。
- **Python 工具**：用 `ffmpeg -f lavfi -i color=...` 造几条测试素材（不同分辨率、含/不含音轨、时长各异），跑完用 `ffprobe` 校验输出**帧数、时长、分辨率**，并用 `ffmpeg -vf select=eq(n\,N)` 抽帧做**像素级抽检**。这是本项目验证的既定手法，改滤镜链后务必照做。

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
2. **平台差异只允许写在 `platform_compat.py`（原「零、平台适配层」）。**
   其它任何位置出现 `if sys.platform == ...` 或 `os.name == "nt"` 都算违规。平台函数**必须支持显式传 `platform=` 参数**，以便在不换系统的前提下验证三个分支。
3. **主流程只有一条，没有可选分支：拼接 → 复刻22 混淆。**
   每条成品 =（封面）+ 前贴×N + 搬运 + 尾贴×N，拼好后一律走 `fieldmix` 混淆输出。
   - 前贴/尾贴：从各自目录随机抽 N 个视频（N 由界面 Spinbox 定，**每条成品独立随机**）；
   - 搬运：搬运目录里**随机抽** N 个（`ui.window.pick_random`，无放回、抽完按目录原顺序执行），
     **一个搬运出一条成品**，条数 N = 界面「随机处理」滑块值（N ≥ 总数 = 全都处理，此时不随机）；
   - 封面：从封面目录随机取 1 张图片，**强制拉伸**替换拼接片第 0 帧（时长/帧数不变，每条独立随机）；
   - 界面固定用 `obfuscate.DEFAULT_ALGORITHM`（= `fieldmix`），`ALGORITHMS` 表里目前只有它一个。
4. **成品规格锁死为参考样本 720×1276 / 30fps**，不随前贴/搬运/尾贴变化。
   各段先重编码成统一规格（缩放补黑边 + 补静音轨），再 concat demuxer `-c copy` 无损拼接。
   封面是「替换第 0 帧」（`overlay=enable='eq(n,0)'` 叠进第一个段的编码），不额外插段、不加时长。
   2026-09-19 用户定案：**高度保持 1276，不改成 1280**（素材分辨率统一，无实际影响），不要再提。
5. **每条产物必须字节级互不相同。**
   fieldmix 每次编码都**重新随机**色块图（`time_ns` 做种子），外加容器 DateUTC 取每条的处理时刻，
   保证 N 条哈希必不同。不做 CRF 抖动、不写死假值。
6. **输出目录默认 `~/Desktop/out`（可用「输出到」更改，不存在自动新建）**，
   命名 = 搬运视频名 + `_` + 两位序号（01~99），序号是**全局递增**的成品序号，便于排序且保证不重名。
7. **进度只吃真实数字，不做估算动画。**
   ffmpeg 真实进度来自 `-progress pipe:1` 的 `out_time=`，`_run()` 边读 stdout 边回调；
   **必须有独立线程排空 stderr**，否则管道写满会死锁。
   - 总进度 =（已完成条数 + 各在跑条目的本条完成度）/ 总条数，见 `proc.blend_progress`；
     **进度条必须从开工第一秒就在爬**，只按整条完成数走会让前面几十分钟看着像卡死。
   - 进度条右边**只显示总进度百分比**，不显示「已完成/总数」条数（用户 2026-09-19 要求；
     只有 `count` 没有细粒度进度时，用已完成条数折算一个百分比顶上）。
   - 每条成品内部：拼接各段占 `fission.CONCAT_WEIGHT`（0.45）、复刻22 混淆占其余，
     由 `process_one_output` 的 `on_progress` 合成一条 0~1 的本条完成度。
   - **上报必须在同一把锁里「算值 + 入队」，且只增不减**：多 worker 并发上报时，
     锁外入队会反序，界面出现「98% 掉回 75%」的倒走（2026-09-19 实测抓到并修复）。
8. **容器混淆只做「改写」，不做二次重编码。**
   一次 x264 重编码（配方逐项对齐样本 SEI，见 `CLONE_X264`）+ 一次纯 Python 容器改写（`_clone_container`）。
   改动滤镜链 / 编码参数后必跑 `python3 video_frame_tool/tests/test_video_frame_tool.py`。
   默认算法 `fieldmix` 复刻 `11 → 22 → 33` 的隔行流程
   （原理、配方与逐场量化数据见 `video_frame_tool/docs/技术原理.md`，本文件不再重复）：
   - 偶行场 = 一整张**静态强彩色色块图**（每成品重新随机；只出 1 帧再 `loop` 复用，否则闪烁且码率暴涨）；
     奇行场 = 原画面。**第 0 帧整帧保留原画** —— 平台用第一帧取封面，第 0 帧若是色块，成品封面就是彩虹横纹。
   - 合成方式：色块图**自带 alpha 行遮罩**（偶行不透明 / 奇行透明）+ `overlay=format=yuv444:enable='gte(n,1)'`。
     `enable` 的 `n` 从 0 起算，`gte(n,1)` 即「第 0 帧不动」；`format=yuv444` 必须显式写，
     默认 `auto` 会走 RGB 混合引入 ±1 级误差。
   - ⚠️ **不要退回 `blend=all_expr='if(eq(mod(Y,2),0),...)'` 的逐像素写法**（2026-09-19 已替换）：
     它让单条编码就吃满十几个核，「同时处理」开多大都没收益。现写法同一素材逐帧 `framemd5` 完全一致，
     单条 CPU 从 55.6s 降到 16.2s、20s 片段墙钟 6.24s → 3.15s（回归测试有断言守着）。
   - 转 `yuv420p` 时必须用 `interl=1` 按场采样色度，否则强彩色上场会污染原画下场。
   - 画面帧标记必须是 BFF（`-top 0`），容器 `FieldOrder` 仍伪装成 TFF；两者相反是样本的关键特征。
   - 按「源帧数 - 1」截帧（`needs_frames=True`），复刻样本丢掉最后一帧的行为。
   - 容器 Duration 写死 1032ms、Tags DURATION 写死 `00:00:01.121`（与样本一致）。
9. **同名覆盖保护必须覆盖所有路径。**
   输出目录选成源目录时，`process_video` 与 `process_one_output` 都要避免同名覆盖源文件。
10. **日志限流 + 状态刷新合并**：一轮消息只刷新最后状态与累计进度；后台日志走队列，不能直接调用操作 Tk 的 `_log()`。
    **每条成品只报「第 N 条：开始处理 / 第 N 条：完成」**（用户 2026-09-19：只要知道正在处理第几个）；
    重编码、改写容器这些内部步骤、以及封面/前贴/搬运/尾贴组成明细都不写日志（`obfuscate.process_video` 因此不写日志，
    `log`/`tag` 参数只作兼容保留）。

## 4. 易踩坑点（血泪教训，改代码前先看）

| 坑 | 说明 |
|---|---|
| **`performance.memory` 是 getter，每次访问返回新快照** | 把它存进变量后在轮询里反复读属性，读到的永远是那一瞬间的冻结值，内存释放了也判不出来。必须**每次重新访问** `performance.memory` |
| **`blob.arrayBuffer()` 会复制整份数据** | 音轨检测只允许对 `blob.slice(0, 2MB)` 调用，直接对整个 Blob 调用等于每路并发再多占一份视频大小的内存 |
| **worker 错峰启动的时间不能太长** | 实测 10 线程 × 80 ms 错峰，跨度 720 ms，遇到 300 ms 就能返回的小文件时并发峰值只有 8。当前 40 ms 是刻意压下来的，调大前先算跨度 |
| **Tk 关闭时序** | `after()` 定时器要在 `WM_DELETE_WINDOW` 里 `after_cancel`，否则关窗时报 `invalid command name ..._poll_queue` |
| **Tk 数值输入框的 `get()` 会抛 TclError** | 输入框被清空或填了非数字时，`IntVar.get()` / `tk.Spinbox` 关联变量的 `get()` 直接抛 `TclError`。`_snapshot()` 在**关窗时**也会被调用，一旦抛出就**关不掉窗口**。所有读输入框的地方都必须 `try/except` 兜住 |
| **输出路径不能等于源路径** | 输出目录若恰好等于源视频所在目录，`<name>.mp4` 会撞上源文件。`process_video` 已有同名检测并自动加 `_已处理` 后缀，改命名逻辑时别删 |
| **`nullsrc` 的尺寸是 `WxH`，不是 `W:H`** | `_fieldmix_pattern` 里写 `size=720:1276` 会直接报 `Error parsing a filter description`。`scale` 才是冒号分隔，两者别混 |
| **concat demuxer 的 list 文件不能写 Windows 反斜杠** | `-f concat` 把反斜杠当转义符，`C:\...\b000.mp4` 会被解析坏（报 No such file）。写 list 时必须 `path.replace("\\", "/")` |
| **ffmpeg 靠扩展名推断输出封装格式** | 临时文件写成 `xxx.mp4.part` 会直接报 `Unable to choose an output format`。临时文件名必须仍以真实扩展名结尾 |
| **macOS 自带 python3 的 Tk 是 8.5，读不了 PNG** | 实测 `/usr/bin/python3`(3.9.6/Tk 8.5)：`PhotoImage(data=png_b64)` 全部报 `couldn't recognize image data`，**Tk 8.5 只认 PPM 文件**。`load_logo()` 有三级回退，删掉 `_png_decode` / `_png_to_ppm_file` 会让 macOS 用户看不到 logo |
| **确认用到的是哪个解释器** | 本机 `python3` 可能指向托管 Python（Tk 9.0），也可能指向 `/usr/bin/python3`（Tk 8.5），两者 logo 加载路径完全不同 |
| **`.DS_Store` / `__pycache__` / `.workbuddy/` 不要提交** | 已在 `.gitignore` 排除。`.workbuddy/` 存本机记忆，含本地绝对路径，**保留在本地但永不入库** |
| **单次计时的结论可能是假的，一律跑 3 轮取中位数** | 同一个配置单轮测出的倍率可能完全相反。**性能结论必须做重复测量**，否则会得出相反的优化方向 |
| **手机竖屏素材是「存储横屏 + 旋转元数据」** | ffprobe 的 `width/height` 是**存储**尺寸，ffmpeg 解码时会按 `rotation` 摆正。横竖判断不能只看存储宽高 |
| **并发不是越多越快** | 一条成品的耗时几乎全在重编码上：实测 4 路 ≈ 1.9x，8 路与 4 路持平、只是更吃内存。`WORKERS_DEFAULT` = 4，「同时处理」调更大不会更快 |
| **进度上报必须「同锁算值 + 入队」且只增不减** | 多个 worker 线程并发上报时，把入队放在锁外会反序进队，界面进度条出现「98% 掉回 75%」的倒走。修复前后实测序列：`…98.8, 75.0, 100` → `…98.8, 100` |

## 5. 代码风格约定

- **注释与文档用中文**，与现有代码保持一致；每个文件顶部有一段说明职责的块注释，每个函数有 docstring（说明参数、返回、副作用）。
- **命名**：JS 用 `camelCase` 函数 / `SCREAMING_SNAKE_CASE` 常量；Python 用 `snake_case` / `SCREAMING_SNAKE_CASE`，模块内 `_private` 前缀表示内部函数。
- **不要引入 emoji 到代码与文档**（扩展日志前缀 `[抖晓晓]` 是既有文案，保留）。
- **注释解释「为什么」，不复述「做了什么」**；涉及性能与时序的数字（阈值、间隔、上限）要在注释里写清来源或实测依据。
- 改动滤镜链 / 并发策略时，**同步更新**：`video_frame_tool/docs/技术原理.md`（原理与配方）、
  受影响的子模块 docstring、本文件的相关条目。任一处不一致即视为未完成。
- **技术文档只有两份，不要再新增**：`video_frame_tool/docs/技术原理.md`（原理、配方、实测数据）
  与 `AGENTS.md`（约束、坑点）。两者分工明确、互不复述；新增说明请并入这两处之一。
- **单元测试 patch 要打到「调用点所在的子模块」**：子模块之间是 `from .x import name` 复制引用，
  patch 到包根（`video_frame_tool._build_small_copy`）**不会生效**。
  新增跨模块依赖时，顺手在测试里确认 patch 目标仍指在调用点上。

## 6. 提交规范

- 分支：`main` 为默认分支。
- commit message 用中文，格式建议 `类型: 简述`，类型取 `feat` / `fix` / `perf` / `docs` / `refactor` / `chore`。
  例：`perf: 素材池加进程内缓存，并发时只扫描一次`
- 涉及性能的改动，在 message 正文附上实测前后对比数据。
- 一次提交只做一件事；格式化改动与功能改动分开。

## 7. 已知待办（未经确认不要擅自处理）

1. **命名不统一**：`manifest.json` 的 `name` 是「抖抖抖 抖音视频下载器 (批量下载)」，而运行时日志与面板标题用「抖晓晓」。统一命名会改变用户在 Chrome 扩展页看到的名字，属于产品决策，**需先与维护者确认**。
2. **`manifest.json` 的 `description` 仍是早期情绪化文案**，若要上架 Chrome 商店需重写。
3. **回归检查尚未接入 CI**：`video_frame_tool/tests/test_video_frame_tool.py` 已覆盖命名、随机抽取、
   拼接规格、封面替换、复刻22 的滤镜配方与产物特征（容器拒读、哈希唯一、场结构）；
   CI（`.github/workflows/ci.yml`）仍只做语法检查。
4. **仓库尚未声明开源许可证**：在维护者决定之前不要添加 `LICENSE` 文件。
