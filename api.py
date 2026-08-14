"""
GPT-SoVITS 语音合成台 API —— 启动入口
====================================
把整个 GPT-SoVITS-API 文件夹放进 GPT-SoVITS 仓库根目录后，在仓库根目录执行:

    python GPT-SoVITS-API/api.py -a 0.0.0.0 -p 9880

也可以进入项目目录后启动（服务会自动定位仓库根目录）:

    cd GPT-SoVITS-API
    python api.py                 # 或: python -m tts_api

实现位于 tts_api/ 包（详见 README.md），本文件仅做入口转发。
"""

from tts_api.main import run

if __name__ == "__main__":
    run()
