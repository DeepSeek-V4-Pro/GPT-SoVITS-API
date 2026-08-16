"""
AI 对话基础组件（测试版）
=========================
- load_voice_persona: 读取音色目录下的默认人设（persona.toml / persona.json / 旧版 persona.txt）
- render_persona: 把结构化人设字段渲染为带严格分区的系统提示词
- _build_chat_url / _call_llm_sync: OpenAI 兼容接口同步调用（含 SSRF 防护）
- _build_models_url / _fetch_models_sync: 自动获取可用模型列表（GET /models，含 SSRF 防护）
- truncate_for_tts / clean_reply_for_tts: 回复文本转 TTS 前的清洗
"""

import json
import os
import re
import unicodedata
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, build_opener

from . import config
from .logging_setup import logger
from .security import _SafeRedirectHandler

# 音色目录下的默认人设文件，按优先级尝试（如 voices/Salt/persona.toml）
PERSONA_FILES = ("persona.toml", "persona.json", "persona.txt")

# 系统提示词区块分隔线
_SECTION_SEP = "=" * 60


def _load_toml_loads():
    """按优先级返回可用的 TOML 解析函数（loads）；均不可用时返回 None。

    兼容: Python 3.11+ 标准库 tomllib；老版本回退到 tomli / tomlkit。
    """
    try:
        import tomllib
        return tomllib.loads
    except ImportError:
        pass
    try:
        import tomli
        return tomli.loads
    except ImportError:
        pass
    try:
        import tomlkit
        return tomlkit.parse
    except ImportError:
        pass
    return None


def _as_dict(v):
    """把 dict / Mapping（如 tomlkit 的 TOMLDocument）统一转成普通 dict。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, Mapping):
        return dict(v)
    return {}


def _parse_persona_file(path: str):
    """解析 persona.toml / persona.json，返回字段字典；解析失败返回 None。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    try:
        if ext == ".json":
            data = json.loads(text)
        elif ext == ".toml":
            loads = _load_toml_loads()
            if loads is None:
                logger.warning("未找到 tomllib/tomli/tomlkit，无法解析人设: %s", path)
                return None
            data = loads(text)
        else:
            return None
    except Exception as e:
        logger.warning("人设文件解析失败（%s）: %s", path, str(e)[:120])
        return None
    return _as_dict(data)


def render_persona(data: dict) -> str:
    """把结构化人设（TOML/JSON 字段）渲染为带严格分区的系统提示词。

    字段说明（对应输出区块）:
      [personality] name / title / identity / background / character / relationship
      [behavior]    behavior_style / reply_style
      [chat]        rules
      [output]      requirements

    空字段对应的区块整体省略；全部为空时返回空串。
    """
    p = _as_dict(data.get("personality"))
    b = _as_dict(data.get("behavior"))
    c = _as_dict(data.get("chat"))
    o = _as_dict(data.get("output"))

    def _t(v):
        return str(v).strip() if v not in (None, "") else ""

    head = ""
    name = _t(p.get("name"))
    title = _t(p.get("title"))
    if name:
        head = "你的名字是" + name
        if title:
            head += "，" + title

    sections = (
        ("1. 身份设定", "Personality", _t(p.get("identity"))),
        ("2. 背景经历", "Background", _t(p.get("background"))),
        ("3. 性格特征", "Character", _t(p.get("character"))),
        ("4. 人物关系与称呼", "Relationship", _t(p.get("relationship"))),
        ("5. 行为准则", "Behavior Style", _t(b.get("behavior_style"))),
        ("6. 说话风格", "Reply Style", _t(b.get("reply_style"))),
        ("7. 聊天注意事项", "Chat Rules", _t(c.get("rules"))),
        ("8. 输出格式要求", "Output Format", _t(o.get("requirements"))),
    )

    lines = []
    if head:
        lines.append(head + "。")
        lines.append("")
    for zh, en, body in sections:
        if not body:
            continue
        lines.append(_SECTION_SEP)
        lines.append(f"【{zh}】{en}".rstrip())
        lines.append(_SECTION_SEP)
        if zh == "7. 聊天注意事项":
            lines.append("在该聊天中的注意事项：")
            lines.append("通用注意事项：")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).strip()


def load_voice_persona(ref_audio_path: str) -> str:
    """读取参考音频所在音色目录下的默认人设文件，渲染为系统提示词返回。

    按优先级查找 persona.toml → persona.json → 旧版 persona.txt；
    都不存在或解析失败返回空串（调用方回退到内置默认人设）。
    """
    if not ref_audio_path:
        return ""
    d = os.path.dirname(os.path.abspath(ref_audio_path))
    for fn in PERSONA_FILES:
        p = os.path.join(d, fn)
        if not os.path.isfile(p):
            continue
        if fn.endswith(".txt"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except OSError:
                return ""
        data = _parse_persona_file(p)
        if data is None:
            continue  # 解析失败时继续尝试下一个文件（如 toml 失败而 json 存在）
        rendered = render_persona(data)
        if rendered:
            return rendered
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
