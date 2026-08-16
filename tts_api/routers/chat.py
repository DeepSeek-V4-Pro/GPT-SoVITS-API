"""
AI 语音对话端点（测试版）
=========================
GET  /chat         AI 语音对话前台（tts_api/frontend/chat.html）
POST /chat         AI 对话 + 语音回复（202 返回 task_id 与回复文本）
POST /chat/test    测试模型接口连通性
POST /chat/models  自动获取可用模型列表（GET {base_url}/models）
GET  /persona      读取音色目录下的 persona.txt 默认人设
"""

import asyncio
import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .. import config, paths
from ..logging_setup import logger
from ..engine import models_ready
from ..llm import (
    _call_llm_sync,
    _fetch_models_sync,
    clean_reply_for_tts,
    load_voice_persona,
    truncate_for_tts,
)
from ..schemas import ChatModelsRequest, ChatRequest, ChatTestRequest
from ..security import (
    _get_client_ip,
    _is_local_ip,
    chat_rate_limiter,
    check_path_traversal,
    check_text_safety,
    is_public_url,
    send_alert,
)
from ..tasks import task_queue
from ..validation import error_response, validate_tts_params

router = APIRouter()


@router.post("/chat/test", summary="测试模型接口连通性（测试版）",
             description="用一条极短消息测试你填写的 OpenAI 兼容接口是否可用（不进行语音合成）。"
                         "Base URL 仅允许公网地址，API Key 仅本次请求使用、不保存。",
             response_description="成功返回 {结果: 成功, 模型: ..., 回复: ...}", tags=["AI 对话"])
async def chat_test(body: ChatTestRequest):
    base_url = (body.base_url or "").strip().rstrip("/")
    api_key = (body.api_key or "").strip()
    if not base_url or not api_key:
        return error_response(400, "请填写 Base URL 与 API Key")
    if not is_public_url(base_url):
        return error_response(400, "Base URL 不合法：仅允许公网 http/https 地址")
    try:
        reply = await asyncio.to_thread(
            _call_llm_sync, base_url, api_key, body.model or "deepseek-v4-pro",
            [{"role": "user", "content": "请只回复两个字：正常"}], 32, 0.1)
    except RuntimeError as e:
        logger.warning("CHAT 测试失败: %s", str(e)[:200])
        return error_response(502, "调用模型服务失败：" + str(e))
    return {"结果": "成功", "模型": body.model, "回复": reply}


@router.post("/chat/models", summary="自动获取可用模型列表（测试版）",
             description="请求你填写的 OpenAI 兼容接口的 GET /models 列表端点，返回可用模型清单"
                         "（自动识别 /v1/models 与 /models 两种地址，404 时回退尝试）。"
                         "Base URL 仅允许公网地址，API Key 仅本次请求使用、不保存。"
                         "各服务商是否支持该端点以其文档为准，不支持时仍可在前台手动输入模型名。",
             response_description="成功返回 {结果: 成功, 数量: N, 模型: [{id, owned_by}]}", tags=["AI 对话"])
async def chat_models(body: ChatModelsRequest):
    base_url = (body.base_url or "").strip().rstrip("/")
    api_key = (body.api_key or "").strip()
    if not base_url or not api_key:
        return error_response(400, "请填写 Base URL 与 API Key")
    if not is_public_url(base_url):
        return error_response(400, "Base URL 不合法：仅允许公网 http/https 地址")
    try:
        models = await asyncio.to_thread(_fetch_models_sync, base_url, api_key)
    except RuntimeError as e:
        logger.warning("CHAT 获取模型列表失败: %s", str(e)[:200])
        return error_response(502, "获取模型列表失败：" + str(e))
    logger.info("CHAT 获取模型列表成功 数量=%d", len(models))
    return {"结果": "成功", "数量": len(models), "模型": models}


@router.get("/persona", summary="获取音色默认人设",
            description="读取参考音频所在音色目录下的 persona.txt 默认人设文件（测试版）。"
                        "文件不存在时返回 404，表示该音色暂无默认人设。",
            response_description="人设纯文本", tags=["AI 对话"])
async def get_persona(ref_audio_path: str):
    if not check_path_traversal(ref_audio_path):
        return error_response(400, "非法的 ref_audio_path")
    persona = load_voice_persona(ref_audio_path)
    if not persona:
        return error_response(404, "该音色目录下没有 persona.txt 默认人设文件")
    return Response(persona, media_type="text/plain; charset=utf-8")


@router.post("/chat", summary="AI 语音对话（测试版）",
             description="文字聊天 + 语音回复（测试版）。**请使用你自己的 OpenAI 兼容 API**："
                         "Base URL 与 API Key 随请求提供，服务端仅中转调用、不保存不记录；"
                         "回复文本经安全检测后提交语音合成任务，202 返回 task_id 与回复文本，"
                         "轮询 /task_status/{task_id} 获取音频。为安全起见，Base URL 仅允许公网地址（SSRF 防护）。"
                         "system_prompt 留空时，自动读取所选音色目录下的 persona.txt 作为默认人设，无此文件则用内置默认人设。"
                         "text_lang 合成语种留空时，默认使用参考音频语种（prompt_lang）。"
                         "history 最多携带最近 20 轮对话；可选 memory_hints 传入浏览器端从全部"
                         "聊天记录中检索出的相关片段（最多 8 条 × 200 字），模型可据此回忆更早的历史内容。",
             response_description="202: 任务信息（task_id / status_url / reply 回复文本 / 排队信息）", tags=["AI 对话"])
async def chat(body: ChatRequest, request: Request):
    ip = _get_client_ip(request)
    text = (body.text or "").strip()
    if not text:
        return error_response(400, "消息不能为空")
    if not check_text_safety(text):
        logger.warning("CHAT 用户消息违禁 ip=%s", ip)
        return error_response(400, "消息包含违规内容")
    base_url = (body.base_url or "").strip().rstrip("/")
    api_key = (body.api_key or "").strip()
    if not base_url:
        return error_response(400, "请先填写模型接口地址 Base URL")
    if not api_key:
        return error_response(400, "请先填写你自己的 API Key")
    if not is_public_url(base_url):
        logger.warning("CHAT 拒绝非公网 base_url ip=%s", ip)
        return error_response(400, "Base URL 不合法：仅允许公网 http/https 地址")
    if not _is_local_ip(ip) and not chat_rate_limiter.check(ip):
        send_alert(f"对话频率限制: IP={ip}")
        return error_response(429, "对话请求过于频繁，请稍后再试")
    if not models_ready():
        return error_response(400, "模型未加载，请先选择音色并点击「加载音色」")

    sys_prompt = (body.system_prompt or "").strip()
    if not sys_prompt:
        sys_prompt = load_voice_persona(body.ref_audio_path or config.DEFAULT_REF_AUDIO_PATH) or config.DEFAULT_CHAT_SYSTEM_PROMPT
    messages = [{"role": "system", "content": sys_prompt}]
    # 从浏览器保存的全部聊天记录中检索到的相关片段：注入为第二段系统消息，
    # 让模型可以在全部历史记忆中搜索、引用（片段本身不参与安全检测，属用户自己的数据）
    hints = body.memory_hints or []
    hint_lines = []
    for h in hints[:config.CHAT_MEMORY_HINTS_MAX]:
        if isinstance(h, dict) and isinstance(h.get("content"), str) and h["content"].strip():
            role = "用户" if h.get("role") == "user" else "AI"
            t = str(h.get("time") or "")
            hint_lines.append(f"[{t}] {role}: {h['content'].strip()[:config.CHAT_MEMORY_HINT_CHARS]}")
    if hint_lines:
        messages.append({"role": "system", "content":
                         "以下是从用户历史聊天记录中检索到的、与当前话题可能相关的片段，"
                         "供你回忆上下文（用户问你「以前聊过什么」之类的问题时，可据此作答）：\n"
                         + "\n".join(hint_lines)})
    history = body.history or []
    for h in history[-config.CHAT_HISTORY_TURNS * 2:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            messages.append({"role": h["role"], "content": h["content"][:1000]})
    messages.append({"role": "user", "content": text})
    logger.info("CHAT ip=%s model=%s 历史=%d 记忆片段=%d 字数=%d",
                ip, body.model, len(messages) - 2, len(hint_lines), len(text))
    try:
        reply = await asyncio.to_thread(
            _call_llm_sync, base_url, api_key, body.model or "deepseek-v4-pro",
            messages, body.max_tokens, body.temperature)
    except RuntimeError as e:
        logger.warning("CHAT LLM 调用失败 ip=%s: %s", ip, str(e)[:200])
        return error_response(502, "调用模型服务失败：" + str(e))
    if not check_text_safety(reply):
        logger.warning("CHAT 模型回复未通过安全检测 ip=%s", ip)
        return error_response(400, "模型回复内容未通过安全检测，请换个话题")
    tts_text = clean_reply_for_tts(truncate_for_tts(reply, config.MAX_TEXT_LENGTH))
    if not tts_text:
        return error_response(400, "模型回复中没有可朗读的内容")

    tts_req = {
        "text": tts_text,
        "text_lang": body.text_lang or body.prompt_lang or "auto",
        "ref_audio_path": body.ref_audio_path or config.DEFAULT_REF_AUDIO_PATH,
        "aux_ref_audio_paths": body.aux_ref_audio_paths or None,
        "prompt_text": body.prompt_text or config.DEFAULT_PROMPT_TEXT,
        "prompt_lang": body.prompt_lang or config.DEFAULT_PROMPT_LANG,
        "gpt_path": (body.gpt_path or "").strip(),
        "sovits_path": (body.sovits_path or "").strip(),
        "speed_factor": body.speed_factor,
        "media_type": body.media_type,
        "streaming_mode": False,
    }
    err = validate_tts_params(tts_req)
    if err:
        return err
    task_id = await task_queue.submit(tts_req, body.media_type, str(request.base_url))
    if not task_id:
        return error_response(503, "排队任务过多，请稍后再试")
    view = task_queue.task_view(task_id)
    view["status_url"] = f"{request.base_url}task_status/{task_id}"
    view["reply"] = reply
    return JSONResponse(status_code=202, content=view)


@router.get("/chat", include_in_schema=False)
async def chat_home():
    """AI 语音对话前台（测试版）"""
    if os.path.isfile(paths.CHAT_HTML):
        return FileResponse(paths.CHAT_HTML, headers={"Cache-Control": "no-cache"})
    return RedirectResponse(url="/")
