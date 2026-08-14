"""
安全组件
========
- RateLimiter / GlobalRateLimiter: 每 IP 与全局频率限制
- is_public_url / _SafeRedirectHandler: SSRF 防护（LLM 中转调用）
- check_path_traversal / check_text_safety: 路径与文本内容校验
- send_alert: 告警（日志 + 可选 Webhook，非阻塞）
- _get_client_ip / _is_local_ip: 客户端 IP 判定工具
"""

import ipaddress
import json
import os
import socket
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen, build_opener, HTTPRedirectHandler

from fastapi import Request

from . import config, paths
from .logging_setup import logger


class RateLimiter:
    def __init__(self, max_requests=config.RATE_LIMIT_MAX, window=config.RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.window = window
        self._records = {}

    def check(self, ip: str) -> bool:
        now = time.time()
        if ip not in self._records:
            self._records[ip] = []
        self._records[ip] = [t for t in self._records[ip] if now - t < self.window]
        if len(self._records[ip]) >= self.max_requests:
            return False
        self._records[ip].append(now)
        return True

    def cleanup(self):
        now = time.time()
        for ip in list(self._records.keys()):
            self._records[ip] = [t for t in self._records[ip] if now - t < self.window]
            if not self._records[ip]:
                del self._records[ip]


rate_limiter = RateLimiter()


class GlobalRateLimiter:
    def __init__(self, max_requests=config.GLOBAL_RATE_LIMIT_MAX, window=config.RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.window = window
        self._records = []

    def check(self) -> bool:
        now = time.time()
        self._records = [t for t in self._records if now - t < self.window]
        if len(self._records) >= self.max_requests:
            return False
        self._records.append(now)
        return True


global_rate_limiter = GlobalRateLimiter()
chat_rate_limiter = RateLimiter(config.CHAT_RATE_LIMIT_MAX, config.CHAT_RATE_LIMIT_WINDOW)


# ============================================================
# SSRF 防护
# ============================================================

_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def is_public_url(url: str) -> bool:
    """SSRF 防护: 仅允许公网 http/https 地址。

    服务端会代用户请求其填写的 base_url，因此必须阻止内网 / 回环 /
    链路本地 / 云元数据 (169.254.169.254) 等地址，防止公网访客借服务器探测内网。
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global or ip in _CGNAT_NET:
            return False
    return True


class _SafeRedirectHandler(HTTPRedirectHandler):
    """重定向同样经过 SSRF 校验，防止 302 跳向内网；
    跨域名重定向时不携带 Authorization，避免 Key 转发给第三方。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_url(newurl):
            raise ValueError("重定向目标不被允许")
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            old_host = urlparse(req.full_url).hostname
            new_host = urlparse(newurl).hostname
            if new_host and old_host and new_host.lower() != old_host.lower():
                new_req.remove_header("Authorization")
        return new_req


# ============================================================
# 路径与文本校验
# ============================================================

def check_path_traversal(path: str) -> bool:
    if not path or ".." in path:
        return False
    abs_path = os.path.abspath(path)
    abs_now = os.path.abspath(paths.NOW_DIR)
    if abs_path == os.path.abspath(config.DEFAULT_REF_AUDIO_PATH):
        return True
    if abs_path == abs_now or abs_path.startswith(abs_now + os.sep):
        return True
    # 额外允许配置的参考音频目录（参考 EXTRA_MODEL_DIRS / REF_SEARCH_DIRS）
    for root in config.REF_SEARCH_DIRS + config.EXTRA_MODEL_DIRS:
        root_abs = os.path.abspath(root)
        if abs_path == root_abs or abs_path.startswith(root_abs + os.sep):
            return True
    return False


def check_text_safety(text: str) -> bool:
    if not text:
        return True
    for kw in config.BLOCKED_KEYWORDS:
        if kw in text:
            return False
    return True


# ============================================================
# 告警
# ============================================================

def send_alert(message: str):
    """发送告警: 写日志 + 可选 Webhook 通知（非阻塞）"""
    logger.error("[ALERT] %s", message)
    if config.WEBHOOK_URL:
        def _post():
            try:
                data = json.dumps({"content": message}).encode()
                req = UrlRequest(config.WEBHOOK_URL, data=data,
                                 headers={"Content-Type": "application/json"})
                urlopen(req, timeout=5)
            except Exception:
                logger.exception("Webhook 发送失败")
        threading.Thread(target=_post, daemon=True).start()


# ============================================================
# 客户端 IP 工具
# ============================================================

_LOCAL_IPS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})


def _get_client_ip(request: Request) -> str:
    """获取真实客户端 IP，优先信任反向代理头"""
    for h in ("X-Forwarded-For", "X-Real-IP"):
        if h in request.headers:
            return request.headers[h].split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_local_ip(ip: str) -> bool:
    return ip in _LOCAL_IPS or ip.startswith("127.")
