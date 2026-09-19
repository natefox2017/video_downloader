# video_downloader

抖音视频/图集批量下载器 + 短视频二次加工工具。

仓库包含两个彼此独立的组件：

| 组件 | 形态 | 作用 |
|---|---|---|
| **抖音批量下载器** | Chrome 扩展（Manifest V3） | 在抖音网页里抓取当前播放过的视频/图集，勾选后多线程批量下载，自动跳过已下载 |
| **视频批处理工具** | Python 桌面 GUI（Tkinter） | 把一批主体视频裂变成多份成品：可随机拼接片头/片尾，再容器混淆（本地不可播放、平台可播），每份哈希必不同 |

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
├── video_frame_tool/       # Python GUI 视频批处理工具（独立子项目，自己一个 README）
├── logo.png                # Python 工具的品牌图（可选；删掉就用包里内嵌的那份）
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

把搬运视频目录里的素材**逐条加工**成成品，每条的结构固定：

> **每条成品 = 封面 + 前贴×N + 搬运 + 尾贴×N → 拼接 → 复刻22 混淆**

| # | 环节 | 说明 |
|---|---|---|
| 1 | **前贴**（可选） | 从前贴目录随机抽 N 个视频拼在搬运前面（每条成品独立随机） |
| 2 | **搬运** | 从搬运目录**按排序取**，**一个搬运出一条成品**，条数 = 界面「只处理前」滑块值 |
| 3 | **尾贴**（可选） | 从尾贴目录随机抽 N 个视频拼在搬运后面（每条成品独立随机） |
| 4 | **封面**（可选） | 从封面目录随机取 1 张图片，强制拉伸后替换拼接片的第 0 帧（时长/帧数不变） |
| 5 | **复刻22 混淆** | 上下场混合（偶行铺静态色块、奇行放原画）+ 容器改写：本地播放器拒读、平台重转码后可播 |

关键规则：

- 主流程只有一条：**拼接 → 复刻22 混淆**，没有分支；
- 搬运目录里有视频才启用「开始」，开始/停止合并为一个按钮；
- 输出目录默认 `~/Desktop/out`（可用「输出到」更改，不存在自动新建），命名 = 搬运名 + `_` + 两位序号；
- 分辨率固定 **720×1276 / 30fps**（对齐参考样本 22.mp4），不随前贴/搬运/尾贴变化；
- 每条产物**哈希必不同**（色块图种子 + 容器 DateUTC 各自随机/取当前时刻）。

原理、配方与实测数据见 [`video_frame_tool/docs/技术原理.md`](video_frame_tool/docs/技术原理.md)。

## 环境要求

- **Python 3.8+**（只用标准库：`tkinter` + `subprocess`，无需 pip 装包）
- **ffmpeg / ffprobe**：优先查 `PATH`，其次各平台常见安装目录，最后回退 pip 包 `imageio-ffmpeg` 自带二进制
  - macOS：`brew install ffmpeg`
  - Windows：`winget install ffmpeg` 或下载官方包解压后加入 `PATH`
  - Linux：`apt install ffmpeg` / `dnf install ffmpeg`

## 运行

Python 工具已从仓库根的单文件拆成独立子项目 **`video_frame_tool/`**（src 布局、按功能分模块、自带回归脚本），
详细说明见 `video_frame_tool/README.md`。

```bash
cd video_frame_tool
./run.sh                          # 一键启动（免安装，含环境自检）
PYTHONPATH=. python3 -m src       # 免安装，直接跑
# 或者装成命令：
pip install -e . && video-frame-tool
```

`run.sh` 会自己把项目根挂到 `PYTHONPATH` 再启动（`-m src`），**不需要先安装**；启动前顺带检查 Python / tkinter / ffmpeg，
并**自动挑一个真正带 tkinter 的解释器**（macOS 上 Homebrew 的 python3 常缺 `python-tk`，会自动改用 conda 或系统自带的）。
指定解释器：`PYTHON=/path/to/python3 ./run.sh`。


## 开发与贡献

- **无构建步骤**：扩展改完代码在 `chrome://extensions/` 点刷新；Python 工具是独立子项目
  `video_frame_tool/`，`PYTHONPATH=. python3 -m src` 直接跑。
- **提交前自检**（CI 也在跑同样的检查）：
  ```bash
  node --check background.js && node --check content.js \
    && node --check injected.js && node --check panel.js
  node -e "JSON.parse(require('fs').readFileSync('manifest.json','utf8'))"
  python3 -m compileall -q video_frame_tool/src video_frame_tool/tests
  python3 video_frame_tool/tests/test_video_frame_tool.py      # 回归（需要 ffmpeg）
  ```
- 改到界面/图标相关代码时，**两个 Tk 版本都要验**（macOS 系统 Tk 8.5 与 Homebrew/托管 Python 的 Tk 8.6+）：
  ```bash
  # 打印 logo 来源与尺寸：Tk 8.5 应显示「（PPM 回退）」，Tk 8.6+ 显示原生路径
  PYTHONPATH=video_frame_tool /usr/bin/python3 -c "import tkinter as tk; import src as m; tk.Tk().withdraw(); print(tk.TkVersion, m.load_logo(64))"
  ```
- 改动较大的功能建议在 commit message 里说明「改了什么 + 为什么」，涉及性能的改动请附实测数据。
- 面向 AI 编码助手的项目约定、架构不变量与易踩坑点见 [AGENTS.md](AGENTS.md)。

## 免责声明

本仓库仅供个人学习与自有内容备份使用。请遵守抖音平台的用户协议与相关法律法规，**不得用于下载、传播侵权内容或任何商业用途**；因使用本工具产生的任何后果由使用者自行承担。视频加工功能涉及的功效性表述须遵守《广告法》等规定，本工具不提供任何内容合规担保。
