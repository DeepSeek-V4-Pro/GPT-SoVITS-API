"""
服务启动入口
============
服务启动流程：
解析命令行参数（config 模块导入时完成）→ 以 uvicorn 启动 FastAPI 应用。
"""

import os
import signal

import uvicorn

from . import config
from .app import app
from .logging_setup import logger


def run():
    ssl_kwargs = {}
    if config.SSL_CERTFILE and config.SSL_KEYFILE:
        ssl_kwargs["ssl_certfile"] = config.SSL_CERTFILE
        ssl_kwargs["ssl_keyfile"] = config.SSL_KEYFILE
    try:
        uvicorn.run(app, host=config.HOST, port=config.PORT, workers=1, log_level="info", **ssl_kwargs)
    except Exception:
        logger.exception("服务启动/运行失败")
        os.kill(os.getpid(), signal.SIGTERM)


if __name__ == "__main__":
    run()
