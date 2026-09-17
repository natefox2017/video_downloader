@echo off
rem ============================================================================
rem  「视频批处理工具」Windows 一键启动脚本（双击本文件即可运行）
rem
rem  用法：
rem     run.bat                    正常启动界面
rem     set VFT_PYTHON=...         指定 Python 解释器（可选）
rem
rem  说明：
rem     1. 自动挑选一个能 import tkinter 的 Python（py / python / python3）
rem     2. 免安装运行：把项目根挂到 PYTHONPATH 后执行 python -m src（包在 src\ 下）
rem     3. ffmpeg/ffprobe 缺失不阻断启动，工具会按内置查找顺序自动定位
rem ============================================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

rem ---------- 定位脚本所在目录（项目根目录，即包根） ----------
set "ROOT=%~dp0"
set "SRC=%ROOT%"

if not exist "%SRC%__init__.py" (
    echo [错误] 找不到包入口：%SRC%__init__.py
    pause
    exit /b 1
)

rem ---------- 挑选可用的 Python 解释器 ----------
set "PY="

rem 显式指定优先
if not "%VFT_PYTHON%"=="" (
    "%VFT_PYTHON%" -c "import tkinter" >nul 2>&1
    if !errorlevel!==0 set "PY=%VFT_PYTHON%"
    if "!PY!"=="" (
        echo [错误] 指定的 Python "%VFT_PYTHON%" 没有 tkinter，无法启动界面。
        echo 请安装带 tkinter 的 Python，或改用其它解释器。
        pause
        exit /b 1
    )
)

rem 依次尝试 py 启动器 / python / python3
if "!PY!"=="" (
    for %%P in (py python python3) do (
        if "!PY!"=="" (
            where %%P >nul 2>&1
            if !errorlevel!==0 (
                %%P -c "import tkinter" >nul 2>&1
                if !errorlevel!==0 set "PY=%%P"
            )
        )
    )
)

if "!PY!"=="" (
    echo [错误] 没有找到可用的 Python（要求带 tkinter）。
    echo 请安装 Python 3 并勾选 "tcl/tk and IDLE"，或设置 VFT_PYTHON 指向可用解释器。
    pause
    exit /b 1
)

rem ---------- 启动界面 ----------
set "PYTHONPATH=%SRC%;%PYTHONPATH%"
echo 使用解释器：!PY!
echo 启动视频批处理工具...
"!PY!" -m src %*

rem 若异常退出，暂停以便查看报错
if not !errorlevel!==0 (
    echo.
    echo [错误] 程序异常退出，请检查上方报错信息。
    pause
)
endlocal
