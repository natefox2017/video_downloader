# video_frame_tool

短视频批处理工具（GUI）。把一批主体视频**裂变**成多份成品：可随机拼接片头/片尾，再对成品做
容器混淆（本地播放器拒读、平台可播），用于同一视频批量投放、规避平台的文件级查重。

- **零第三方依赖**：只用 Python 标准库 + 外部 `ffmpeg` / `ffprobe`
- Windows / macOS / Linux 通用，代码中不写死任何素材路径
- 界面用 tkinter，配置记在 `settings.json`，下次启动自动回填

## 目录结构

```
video_frame_tool/
├── pyproject.toml                    打包配置（src 布局）
├── run.sh                            一键启动（macOS/Linux，免安装，含环境自检）
├── run.bat                           一键启动（Windows，双击即可）
├── README.md
├── src/
│   └── video_frame_tool/
│       ├── __init__.py               重新导出所有顶层名字
│       ├── __main__.py               python -m video_frame_tool 入口
│       ├── platform_compat.py        零、平台适配层（唯一的 sys.platform 分支处）
│       ├── constants.py              一、默认参数与常量
│       ├── settings.py               settings.json 读写
│       ├── logo.py                   程序图标
│       ├── ffmpeg_bin.py             ffmpeg / ffprobe 定位
│       ├── probe.py                  媒体信息探测（时长/尺寸/帧率/旋转/目录扫描）
│       ├── proc.py                   可中断的子进程调用与取消检查
│       ├── obfuscate.py              容器混淆（算法注册表 + 逐字复刻样本）
│       ├── fission.py                「片头 + 主体 + 片尾」拼接 + 混淆裂变
│       └── ui/window.py              图形界面 App
├── docs/混淆原理与流程.md             混淆原理与实现
└── tests/test_video_frame_tool.py    回归检查
```

## 主界面：两大模块、三个独立开关

### 模块一「拼接片头片尾」

- **片头**（可开关）：从片头文件夹随机抽 N 个视频拼在主体前面，N 可指定（1~10）
- **片尾**（可开关）：从片尾文件夹随机抽 N 个视频拼在主体后面，N 可指定（1~10）
- 片头 / 片尾每份独立随机抽取，三个功能（片头 / 片尾 / 混淆）任意组合、可只开其中一个

### 模块二「混淆视频」

- **混淆**（可开关）：把主体视频裂变成 M 份（M 可指定 1~99），每份做容器混淆 →
  本地播放器拒读、平台重转码后可播
- 不勾混淆时输出**标准 MP4**（本地可播），片头/片尾拼接照常生效
- **混淆算法**（radio 单选，按日期命名）：当前只有 `260917` 一个
- **只混淆前 N 个**（slider）：默认 = 文件夹里视频总数，拖到 N 只处理排序后的前 N 个

### 关键规则

- **三开关全关时禁用「开始」按钮**，不允许空跑
- **开始 / 停止合并为一个按钮**（运行中显示「停止」）
- **同时处理**：并发数默认 = CPU 核数的一半（上限 8）
- **输出到**：按钮右侧显示输出路径；默认 `~/Desktop/out`，未选择时自动落这里，目录不存在自动新建
- 分辨率固定 **720×1276**（对齐参考样本），不随片头/主体/片尾变化
- 命名 = 主体视频名 + `_` + 两位序号（`foo_01.mp4` ~ `foo_99.mp4`）
- 每份产物文件哈希互不相同
- 总产出 = 主体视频数 × 混淆份数

原理与实现的完整说明见 [`docs/混淆原理与流程.md`](docs/混淆原理与流程.md)。

## 安装与运行

```bash
# 方式一：一键启动（推荐，免安装）
./run.sh        # macOS / Linux
run.bat         # Windows（双击即可，或命令行运行）

# 方式二：不安装，直接用模块跑
PYTHONPATH=src python3 -m src

# 方式三：装成命令（之后随处可用）
pip install -e .
video-frame-tool
```

`run.sh`（macOS/Linux）和 `run.bat`（Windows）都会自动把 `src` 挂到 `PYTHONPATH` 并做环境自检
（Python / tkinter / ffmpeg），缺什么直接给修复命令。

- **会自己挑解释器**：不是「PATH 里第一个 python3」就用。macOS 上 Homebrew 的 `python3` 常常没装 `python-tk`，
  脚本会逐个试 `import tkinter`，挑第一个能用的。
- 指定解释器（不再回退，缺 tkinter 直接报错）：`PYTHON=/path/to/python3 ./run.sh`；Windows 上设 `set VFT_PYTHON=C:\...\python.exe` 后运行
- 只看会用哪个解释器、跑什么命令（不启动界面）：`VFT_DRY_RUN=1 ./run.sh`

## 开发约定

- **回归检查**：`python3 tests/test_video_frame_tool.py`
  只用标准库与临时目录，不碰用户素材。
- **改滤镜链**必须跑像素级抽帧断言。
- **性能结论必须重复测量取中位数**，单轮计时会骗人。

## 实现要点

- **容器混淆**（`obfuscate.py`）：一次 x264 重编码（配方逐项对齐参考样本的 SEI），
  再把容器改写成样本形状——分辨率元数据抹成 Void → 本地播放器拒读、平台重转码后可播。
  元数据用**真实时长 + 当前时间**（对齐能过查重的原版行为）。
- **混淆算法可扩展**：算法按「通过查重实测的日期」命名（如 `260917`），注册在 `ALGORITHMS`
  表里，界面 radio 自动生成选项。加新算法 = 新增一个算法对象 + 注册一行，不动已有算法。
- **拼接**（`fission.py`）：片头 + 主体 + 片尾各段先重编码成固定 720×1276 / 30fps，
  再 concat demuxer 无损拼接；无音轨的段补静音轨。
- **进度只吃真实数字**：编码来自 ffmpeg `-progress pipe:1` 的 `out_time`，
  `_run()` 边读 stdout 边回调，另有独立线程排空 stderr 防管道写满死锁。
- **配置记忆**：用户上次选择的路径与参数记在 `settings.json`（位置见
  `platform_compat.py` 的 `user_config_dir`），下次启动自动回填。
