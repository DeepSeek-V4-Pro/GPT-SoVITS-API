"""
日志初始化
==========
控制台 + 按天滚动文件（保留 30 天），日志名 "tts_api"。
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from . import paths


def _init_logger():
    log = logging.getLogger("tts_api")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    log.addHandler(console)
    fh = TimedRotatingFileHandler(
        os.path.join(paths.LOG_DIR, "api.log"), when="midnight", interval=1,
        backupCount=30, encoding="utf-8",
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    return log


logger = _init_logger()
