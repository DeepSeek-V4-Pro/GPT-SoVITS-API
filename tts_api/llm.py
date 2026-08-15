"""
AI 对话基础组件（测试版）
=========================
- load_voice_persona: 读取音色目录下的 persona.txt 默认人设
- _build_chat_url / _call_llm_sync: OpenAI 兼容接口同步调用（含 SSRF 防护）
- _build_models_url / _fetch_models_sync: 自动获取可用模型列表（GET /models，含 SSRF 防护）
- truncate_for_tts / clean_reply_for_tts: 回复文本转 TTS 前的清洗
"""

import json
import os
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, build_opener

from . import config
from .logging_setup import logger
from .security import _SafeRedirectHandler

# 音色目录下的默认人设文件名（如 voices/我的音色/persona.txt）
PERSONA_FILE = "persona.txt"


def load_voice_persona(ref_audio_path: str) -> str:
    """读取参考音频所在音色目录下的默认人设文件 persona.txt；不存在返回空串。"""
    if not ref_audio_path:
        return ""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(ref_audio_path)), PERSONA_FILE)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except OSError:
        pass
    return ""


def _build_chat_url(base_url: str) -> str:
    """把用户填写的 Base URL 归一化成 chat/completions 地址。"""
    u = base_url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if re.search(r"/v\d+$", u):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


def _call_llm_sync(base_url: str, api_key: str, model: str,
                   messages: list, max_tokens: int, temperature: float) -> str:
    """同步调用 OpenAI 兼容 chat/completions 接口，返回回复文本。失败抛 RuntimeError。

    注意: api_key 只在本函数内存中使用，绝不写入日志或磁盘。
    """
    url = _build_chat_url(base_url)
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = UrlRequest(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    opener = build_opener(_SafeRedirectHandler())
    try:
        with opener.open(req, timeout=config.CHAT_LLM_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:120]
        except Exception:
            pass
        msg = {
            401: "API Key 无效或没有权限",
            403: "访问被拒绝，请检查 Key 权限或地区限制",
            404: "接口地址不存在，请检查 Base URL 与模型名",
            429: "请求过于频繁或额度不足",
        }.get(e.code, "模型服务返回错误（%d）" % e.code)
        raise RuntimeError(msg + ("：" + detail if detail else ""))
    except (URLError, TimeoutError, OSError, ValueError) as e:
        raise RuntimeError("无法连接模型服务：" + str(e)[:160])
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("模型返回格式无法解析")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("模型返回内容为空")
    return content.strip()


def _build_models_url(base_url: str) -> str:
    """把 Base URL 归一化成 GET /models 地址（优先 OpenAI 兼容的 v1 风格）。"""
    u = base_url.strip().rstrip("/")
    if u.endswith("/models"):
        return u
    if re.search(r"/v\d+$", u):
        return u + "/models"
    return u + "/v1/models"


def _alt_models_url(base_url: str) -> str:
    """备选地址: 根路径下的 /models（部分网关不提供 /vN/models）。"""
    u = base_url.strip().rstrip("/")
    if u.endswith("/models"):
        return u
    u = re.sub(r"(/v\d+)+$", "", u)  # 去掉路径中的版本段（/v1 /v4 等）
    return u + "/models"


def _fetch_models_sync(base_url: str, api_key: str) -> list:
    """同步获取 OpenAI 兼容接口的可用模型列表（GET /models）。

    返回 [{"id": 模型名, "owned_by": 提供方}] 列表；失败抛 RuntimeError。
    api_key 只在本函数内存中使用，绝不写入日志或磁盘。
    """
    urls = []
    for u in (_build_models_url(base_url), _alt_models_url(base_url)):
        if u not in urls:
            urls.append(u)
    opener = build_opener(_SafeRedirectHandler())
    for url in urls:
        req = UrlRequest(url, method="GET", headers={"Authorization": "Bearer " + api_key})
        try:
            with opener.open(req, timeout=config.CHAT_MODELS_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            # 404: 该地址没有 /models，尝试下一个备选地址
            if e.code == 404 and url != urls[-1]:
                continue
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:120]
            except Exception:
                pass
            msg = {
                401: "API Key 无效或没有权限",
                403: "访问被拒绝，请检查 Key 权限或地区限制",
                404: "该服务不支持获取模型列表",
                429: "请求过于频繁或额度不足",
            }.get(e.code, "模型服务返回错误（%d）" % e.code)
            raise RuntimeError(msg + ("：" + detail if detail else ""))
        except (URLError, TimeoutError, OSError, ValueError) as e:
            raise RuntimeError("无法连接模型服务：" + str(e)[:160])
        try:
            data = json.loads(body)
        except Exception:
            raise RuntimeError("模型列表返回格式无法解析")
        items = None
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            items = data["data"]          # OpenAI 兼容格式: {data: [{id, ...}]}
        elif isinstance(data, dict) and isinstance(data.get("models"), list):
            items = data["models"]        # 部分网关使用 {models: [{name/id, ...}]}
        if not items:
            raise RuntimeError("模型列表为空或格式无法解析")
        out = []
        for m in items:
            if not isinstance(m, dict):
                continue
            mid = m.get("id") or m.get("name") or ""
            if isinstance(mid, str):
                mid = mid.strip()
            if not mid:
                continue
            if mid.startswith("models/"):  # Gemini 风格的 "models/xxx" 前缀
                mid = mid[len("models/"):]
            owned = m.get("owned_by") or ""
            out.append({"id": mid, "owned_by": owned if isinstance(owned, str) else ""})
        if not out:
            raise RuntimeError("模型列表为空或格式无法解析")
        out.sort(key=lambda x: x["id"].lower())
        return out


def truncate_for_tts(text: str, limit: int) -> str:
    """超过 TTS 文本上限时，在句末标点处截断，避免话说到一半。"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = list(re.finditer(r"[。！？!?…\n]", head))
    if ends:
        return head[: ends[-1].end()].strip()
    return head.strip()


def clean_reply_for_tts(text: str) -> str:
    """清理模型回复: 去 Markdown / 列表符号 / emoji 等不可朗读字符。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^\s{0,3}#+\s*", "", text, flags=re.M)
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"~{2}([^~\n]+)~{2}", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\d+[.、)）]\s*", "", text, flags=re.M)
    # 先合并空白（保留句子间空格），再去除 emoji 等符号类与格式类字符
    text = re.sub(r"\s+", " ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("S", "C"))
    return text.strip()
