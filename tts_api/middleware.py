"""
HTTP 安全中间件
===============
IP 黑/白名单、Referer 校验、每 IP / 全局限流、请求体大小限制。
"""

from fastapi import Request
from fastapi.responses import JSONResponse

from . import config
from .logging_setup import logger
from .security import (
    _get_client_ip,
    _is_local_ip,
    global_rate_limiter,
    rate_limiter,
    send_alert,
)

# 每 IP 频率限制只作用于重资源端点（合成/切换/对话/反馈）。
# 轮询（task_status / switch_status / health）、音频下载（audio）、静态资源
# 不占用配额：否则手机端任务轮询 + 切换横幅轮询很容易把音频请求挤成 429，
# 导致「合成成功但无法播放」。
RATE_LIMITED_PATHS = (
    "/tts", "/play", "/chat", "/chat/models", "/chat/test",
    "/set_voice", "/set_gpt_weights", "/set_sovits_weights",
    "/feedback",
)


async def security_middleware(request: Request, call_next):
    ip = _get_client_ip(request)

    # IP 黑名单
    if ip in config.BLOCKED_IPS:
        logger.warning("黑名单 IP 拦截: %s", ip)
        return JSONResponse(status_code=403, content={"错误": "禁止访问"})

    # 本地请求始终放行
    if not _is_local_ip(ip):
        # IP 白名单（仅影响 /tts /play 端点）
        if config.ALLOWED_IPS and request.url.path in ("/tts", "/play"):
            if ip not in config.ALLOWED_IPS:
                logger.warning("IP 不在白名单: %s", ip)
                return JSONResponse(status_code=403, content={"错误": "禁止访问"})

        # Referer 校验
        if config.REQUIRE_REFERER and request.url.path in ("/tts", "/play"):
            ref = request.headers.get("referer", "")
            if not ref:
                logger.warning("缺少 Referer IP=%s", ip)
                return JSONResponse(status_code=400, content={"错误": "缺少 Referer 头"})

        # 频率限制（仅重资源端点）
        if request.url.path in RATE_LIMITED_PATHS and not rate_limiter.check(ip):
            logger.warning("频率限制拦截 IP=%s 路径=%s", ip, request.url.path)
            send_alert(f"频率限制拦截: IP={ip} 路径={request.url.path}")
            return JSONResponse(status_code=429, content={"错误": "请求过于频繁，请稍后再试"})

        # 全局限量
        if request.url.path in ("/tts", "/play") and not global_rate_limiter.check():
            logger.warning("全局限量拦截 IP=%s", ip)
            send_alert(f"全局限量已达: IP={ip}")
            return JSONResponse(status_code=503, content={"错误": "服务繁忙，请稍后再试"})

        # 请求体大小
        cl = request.headers.get("content-length")
        if cl:
            try:
                cl_int = int(cl)
            except ValueError:
                cl_int = config.MAX_BODY_SIZE + 1
            if cl_int > config.MAX_BODY_SIZE:
                logger.warning("请求体过大拦截 IP=%s size=%s", ip, cl)
                return JSONResponse(status_code=413, content={"错误": "请求体过大"})
    return await call_next(request)
