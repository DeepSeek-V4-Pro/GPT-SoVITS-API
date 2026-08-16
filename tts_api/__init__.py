"""
GPT-SoVITS 语音合成台 API（tts_api 包）
=======================================
基于 GPT-SoVITS 引擎的轻量自托管语音合成 HTTP 服务。

启动方式（把本项目放进 GPT-SoVITS 仓库根目录后）:
    python GPT-SoVITS-API/api.py -a 0.0.0.0 -p 9880   # 仓库根目录启动（推荐）
    cd GPT-SoVITS-API && python api.py -a 0.0.0.0 -p 9880
    cd GPT-SoVITS-API && python -m tts_api -a 0.0.0.0 -p 9880

前台: http://127.0.0.1:9880/   文档: http://127.0.0.1:9880/docs
"""

__version__ = "1.4"
