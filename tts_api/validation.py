"""
请求校验
========
- validate_tts_params: TTS 请求参数校验（路径 / 文本 / 语言 / 格式）
- resolve_streaming_params: streaming_mode 参数标准化
- error_response: 统一的 {"错误": ...} JSON 错误响应
"""

import shutil

from fastapi.responses import JSONResponse

from . import config
from .logging_setup import logger
from .engine import tts_config, cut_method_names
from .security import check_path_traversal, check_text_safety, send_alert
from .voice_library import is_allowed_model_path


def error_response(status: int, message: str):
    return JSONResponse(status_code=status, content={"错误": message})


def validate_tts_params(req: dict):
    lang_map = {"jp": "ja", "japanese": "ja", "cn": "all_zh", "kr": "ko"}
    pure_map = {"zh": "all_zh", "ja": "all_ja", "yue": "all_yue", "ko": "all_ko"}
    for key in ("text_lang", "prompt_lang"):
        if key in req and req[key]:
            code = lang_map.get(req[key].lower(), req[key])
            req[key] = pure_map.get(code, code)
    if not req.get("ref_audio_path"):
        return error_response(400, "缺少参数: ref_audio_path")
    if not check_path_traversal(req["ref_audio_path"]):
        return error_response(400, "非法的 ref_audio_path")
    for key in ("gpt_path", "sovits_path"):
        p = (req.get(key) or "").strip()
        if p and not is_allowed_model_path(p):
            return error_response(400, f"非法的模型路径: {key}")
    aux_paths = req.get("aux_ref_audio_paths")
    if aux_paths:
        for p in aux_paths:
            if not check_path_traversal(p):
                return error_response(400, f"非法的辅助音频路径: {p}")
    if not req.get("text"):
        return error_response(400, "缺少参数: text")
    if len(req["text"]) > config.MAX_TEXT_LENGTH:
        if config.MAX_TEXT_LENGTH > 0:
            logger.warning("文本过长截断: %d > %d", len(req["text"]), config.MAX_TEXT_LENGTH)
            req["text"] = req["text"][:config.MAX_TEXT_LENGTH]
    if not check_text_safety(req["text"]):
        logger.warning("文本违禁拦截: %s...", req["text"][:50])
        send_alert(f"文本违禁: text={req['text'][:40]}")
        return error_response(400, "文本包含违规内容")
    text_lang = req.get("text_lang", "").lower()
    if not text_lang:
        return error_response(400, "缺少参数: text_lang")
    if text_lang not in tts_config.languages:
        return error_response(400, f"不支持的语言: '{text_lang}'")
    prompt_lang = req.get("prompt_lang", "").lower()
    if not prompt_lang:
        return error_response(400, "缺少参数: prompt_lang")
    if prompt_lang not in tts_config.languages:
        return error_response(400, f"不支持的语言: '{prompt_lang}'")
    if req.get("media_type", "wav") not in ("wav", "raw", "ogg", "aac"):
        return error_response(400, "不支持的音频格式")
    if req.get("media_type") == "aac" and not shutil.which("ffmpeg"):
        return error_response(400, "服务端未安装 ffmpeg，暂不支持 aac 格式，请改用 wav / ogg")
    if req.get("text_split_method", "cut0") not in cut_method_names:
        return error_response(400, "不支持的文本切分方式")
    return None


def resolve_streaming_params(req: dict):
    """解析 streaming_mode 参数，将 req 中的字段标准化。返回 error_response 或 None。"""
    streaming_mode = req.get("streaming_mode", False)
    return_fragment = req.get("return_fragment", False)
    mode_map = {
        0: (False, False, False),
        1: (False, True, False),
        2: (True, False, False),
        3: (True, False, True),
    }
    if isinstance(streaming_mode, int) and streaming_mode in mode_map:
        streaming_mode, return_fragment, fixed_length_chunk = mode_map[streaming_mode]
    elif streaming_mode in (True, False):
        fixed_length_chunk = False
    else:
        return error_response(400, "streaming_mode 必须为 0~3 或 true/false")
    req["streaming_mode"] = streaming_mode
    req["return_fragment"] = return_fragment
    req["fixed_length_chunk"] = fixed_length_chunk
    return None
