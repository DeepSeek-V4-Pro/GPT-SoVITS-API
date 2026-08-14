"""
FastAPI 应用装配
================
创建应用、注入文档与 Swagger 配置、CORS、静态资源、安全中间件，
注册全部路由，并通过 lifespan 管理启动 / 关闭流程。
"""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles

from . import config, paths
from .logging_setup import logger
from .docs import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    CONTACT,
    LICENSE_INFO,
    SWAGGER_CSS_URL,
    SWAGGER_UI_PARAMETERS,
)
from .middleware import security_middleware
from .security import chat_rate_limiter, rate_limiter
from .engine import model_switch_guard
from .tasks import task_queue
from .routers import chat, models, system, tts


async def cleanup_task():
    """每小时执行: 清理过期音频 + 清理过期 IP 记录"""
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        cleaned = 0
        for f in os.listdir(paths.TEMP_AUDIO_DIR):
            fpath = os.path.join(paths.TEMP_AUDIO_DIR, f)
            if not os.path.isfile(fpath):
                continue
            try:
                if now - os.path.getmtime(fpath) > 3600:
                    os.remove(fpath)
                    cleaned += 1
            except OSError:
                pass
        if cleaned:
            logger.info("[cleanup] 已删除 %d 个过期音频", cleaned)
        task_queue.cleanup()
        rate_limiter.cleanup()
        chat_rate_limiter.cleanup()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup = asyncio.create_task(cleanup_task())
    task_queue.start()
    await model_switch_guard.start()
    logger.info("合成任务队列已启动: 并发=%d 排队上限=%d", config.MAX_CONCURRENT, config.MAX_QUEUE_SIZE)
    proto = "https" if config.SSL_CERTFILE else "http"
    logger.info("服务启动完成，监听 %s://%s:%d", proto, config.HOST, config.PORT)
    if not config.SSL_CERTFILE:
        logger.warning("未启用 HTTPS，流量将以明文传输！推荐使用 --ssl-certfile 和 --ssl-keyfile")
    if config.AUDIO_AUTH_TOKEN:
        logger.info("音频文件访问鉴权已启用")
    if config.SWITCH_AUTH_TOKEN:
        logger.info("音色切换鉴权已启用")
    if config.ALLOWED_IPS:
        logger.info("IP 白名单已启用: %d 条", len(config.ALLOWED_IPS))
    if config.WEBHOOK_URL:
        logger.info("告警 Webhook 已配置")
    yield
    await model_switch_guard.stop()
    cleanup.cancel()
    try:
        await cleanup
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    contact=CONTACT,
    license_info=LICENSE_INFO,
    docs_url=None,  # /docs 由下方自定义路由提供（合成台配色主题）
    lifespan=lifespan,
)

# CORS: 默认禁止跨域，通过 --cors-origins 指定允许的域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS if config.CORS_ORIGINS else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# 合成台静态素材（tts_api/frontend/assets/ 下的主题等静态文件）
if os.path.isdir(paths.STUDIO_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=paths.STUDIO_ASSETS_DIR), name="assets")

# 安全中间件（黑/白名单、Referer、限流、请求体大小）
app.middleware("http")(security_middleware)

# 路由注册
app.include_router(system.router)
app.include_router(models.router)
app.include_router(tts.router)
app.include_router(chat.router)


# ============================================================
# /docs 自定义 Swagger UI
# ============================================================
# FastAPI 的 swagger_ui_parameters 只作用于 SwaggerUIBundle 配置，
# 无法注入 customCss，因此这里用 get_swagger_ui_html 接管 /docs，
# 主题 CSS 随包分发（frontend/assets/swagger_theme.css，配色与前台一致）。
@app.get("/docs", include_in_schema=False)
async def swagger_ui_home():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=API_TITLE,
        swagger_css_url=SWAGGER_CSS_URL,
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    )
