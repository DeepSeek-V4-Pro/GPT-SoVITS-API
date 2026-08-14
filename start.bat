@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================
rem  GPT-SoVITS 语音合成台 API 启动脚本（Windows）
rem  运行一次 install_deps.py 后，下面的 PYTHON_EXE
rem  会被自动替换为你的 Python 解释器路径；
rem  也可以手动修改下面的占位符。
rem ============================================
set "PYTHON_EXE=python"

rem 示例（conda 环境，按需取消注释并修改）：
rem set "PYTHON_EXE=D:\你的路径\miniconda3\envs\GPTSoVits\python.exe"

where "%PYTHON_EXE%" >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python 解释器: %PYTHON_EXE%
    echo 请安装 Python 后重试，或在 start.bat 中把 PYTHON_EXE 改为你的 Python 路径。
    pause
    exit /b 1
)

rem 首次运行：自动检查并安装本项目额外依赖
rem （GPT-SoVITS 本体依赖请先按官方教程安装）
"%PYTHON_EXE%" -c "import fastapi, uvicorn, pydantic, soundfile" >nul 2>nul
if errorlevel 1 (
    echo [首次运行] 检测到缺少本项目依赖，正在自动安装，请稍候 ...
    set "TTS_API_LAUNCHER_RUN=1"
    "%PYTHON_EXE%" install_deps.py --yes
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请手动执行: "%PYTHON_EXE%" install_deps.py
        pause
        exit /b 1
    )
)

netstat -ano | findstr ":9880" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [提示] 端口 9880 已被占用，服务可能已经在运行。
    echo 请先关闭旧的服务窗口，或直接访问 http://127.0.0.1:9880/
    pause
    exit /b 1
)

echo 正在启动 GPT-SoVITS 语音合成台 API ...
echo.
echo   前台页面: http://127.0.0.1:9880/
echo   API 文档: http://127.0.0.1:9880/docs
echo   健康检查: http://127.0.0.1:9880/health
echo.
echo 停止服务请按 Ctrl+C
echo ================================================
"%PYTHON_EXE%" api.py -a 0.0.0.0 -p 9880
set "EXITCODE=%errorlevel%"
echo ================================================
if "%EXITCODE%"=="0" (
    echo 服务已正常停止。
) else (
    echo [错误] 服务异常退出，退出码: %EXITCODE%
    echo 请保留上面的报错信息以便排查。
)
pause
