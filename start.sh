#!/usr/bin/env bash
# GPT-SoVITS 语音合成台 API 启动脚本（Linux / macOS）
# 可用环境变量 PYTHON_EXE 指定 Python 解释器（默认 python3）
# 运行一次 install_deps.py 后，默认解释器会被自动写入 PYTHON_EXE_DEFAULT。
cd "$(dirname "$0")" || exit 1
PYTHON_EXE="${PYTHON_EXE:-python3}"

# 首次运行：自动检查并安装本项目额外依赖
# （GPT-SoVITS 本体依赖请先按官方教程安装）
if ! "$PYTHON_EXE" -c "import fastapi, uvicorn, pydantic, soundfile" >/dev/null 2>&1; then
    echo "[首次运行] 检测到缺少本项目依赖，正在自动安装，请稍候 ..."
    TTS_API_LAUNCHER_RUN=1 "$PYTHON_EXE" install_deps.py --yes || {
        echo "[错误] 依赖安装失败，请手动执行: $PYTHON_EXE install_deps.py"
        exit 1
    }
fi

exec "$PYTHON_EXE" api.py -a 0.0.0.0 -p 9880
