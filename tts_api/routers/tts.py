"""
语音合成端点
============
GET  /                    语音合成台前台（tts_api/frontend/index.html）
GET  /tts                 查询参数调用，同步等待返回音频；流式模式返回音频流
POST /tts                 提交 JSON → 202 任务 ID（轮询取结果）；流式模式返回音频流
GET  /task_status/{task_id} 轮询任务状态
GET  /audio/{filename}    下载临时音频（完整 200 响应，手机兼容优先）
GET  /play                浏览器在线试听
GET  /favicon.ico         空响应占位
"""

import os
import time
import uuid
from io import BytesIO
from typing import Union

from fastapi import APIRouter, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from .. import config, paths
from ..logging_setup import logger
from ..audio import pack_audio
from ..engine import models_ready
from ..schemas import TTSTaskInfo, TTSRequest
from ..security import _get_client_ip
from ..synth import (
    _stream_audio_generator,
    generate_audio_data,
    handle_tts,
    safe_audio_path,
)
from ..tasks import task_queue
from ..validation import error_response, resolve_streaming_params, validate_tts_params

router = APIRouter()


@router.get("/tts", summary="通过 GET 请求合成语音",
            description="通过 URL 查询参数调用语音合成，适用于 curl/wget 等命令行工具。设置 `streaming_mode=1/2/3` 可启用流式响应。推荐使用 POST /tts 以获得更好体验。",
            response_description="成功返回音频文件（二进制）；流式模式返回音频流（chunked transfer encoding）", tags=["语音合成"])
async def tts_get(
    text: str, text_lang: str, ref_audio_path: str = config.DEFAULT_REF_AUDIO_PATH, prompt_lang: str = config.DEFAULT_PROMPT_LANG,
    prompt_text: str = config.DEFAULT_PROMPT_TEXT, aux_ref_audio_paths: str = None,
    gpt_path: str = "", sovits_path: str = "",
    top_k: int = 15, top_p: float = 1, temperature: float = 0.6,
    text_split_method: str = "cut0", batch_size: int = 20,
    batch_threshold: float = 0.75, split_bucket: bool = True,
    speed_factor: float = 1.0, fragment_interval: float = 0.3,
    seed: int = -1, media_type: str = "wav", parallel_infer: bool = True,
    repetition_penalty: float = 1.35, sample_steps: int = 32,
    super_sampling: bool = False, streaming_mode: Union[bool, int] = False,
    overlap_length: int = 2, min_chunk_length: int = 16,
):
    req = {
        "text": text, "text_lang": text_lang.lower(),
        "ref_audio_path": ref_audio_path,
        "prompt_text": prompt_text, "prompt_lang": prompt_lang.lower(),
        "top_k": int(top_k), "top_p": float(top_p),
        "temperature": float(temperature),
        "text_split_method": text_split_method,
        "batch_size": int(batch_size),
        "batch_threshold": float(batch_threshold),
        "split_bucket": split_bucket,
        "speed_factor": float(speed_factor),
        "fragment_interval": float(fragment_interval),
        "seed": int(seed), "media_type": media_type,
        "parallel_infer": parallel_infer,
        "repetition_penalty": float(repetition_penalty),
        "sample_steps": int(sample_steps),
        "super_sampling": super_sampling,
        "streaming_mode": streaming_mode,
        "overlap_length": int(overlap_length),
        "min_chunk_length": int(min_chunk_length),
        "aux_ref_audio_paths":
            aux_ref_audio_paths.split(",") if aux_ref_audio_paths else None,
        "gpt_path": gpt_path,
        "sovits_path": sovits_path,
    }
    return await handle_tts(req)


@router.post("/tts", summary="合成语音（推荐使用）",
             description="提交 JSON 参数合成语音。非流式模式：校验通过后立即返回 **202** 与 task_id/排队信息，"
                         "请轮询 `GET /task_status/{task_id}` 获取结果；`status=done` 时返回 play_url 与 download_url。"
                         "流式模式（streaming_mode=1/2/3）直接返回音频流。",
             response_description="202: 任务信息（task_id / status_url / 排队位置 / 预计秒数）；流式模式返回音频二进制流",
             response_model=TTSTaskInfo, status_code=202, tags=["语音合成"])
async def tts_post(req: Request, body: TTSRequest):
    req_dict = body.model_dump()
    is_stream = req_dict.get("streaming_mode", False) or req_dict.get("return_fragment", False)
    t0 = time.time()
    ip = _get_client_ip(req)
    try:
        if is_stream:
            if not models_ready():
                return error_response(400, "模型未加载，请先在页面选择音色并点击「加载音色」")
            err = validate_tts_params(req_dict)
            if err:
                return err
            err = resolve_streaming_params(req_dict)
            if err:
                return err
            mime = body.media_type if body.media_type != "aac" else "aac"
            return StreamingResponse(
                _stream_audio_generator(req_dict),
                media_type=f"audio/{mime}",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                         "Content-Disposition": "attachment; filename=tts_output.wav"},
            )
        # 非流式: 先校验，再入队异步合成，立即返回 202 + 排队信息
        if not models_ready():
            return error_response(400, "模型未加载，请先在页面选择音色并点击「加载音色」")
        err = validate_tts_params(req_dict)
        if err:
            return err
        err = resolve_streaming_params(req_dict)
        if err:
            return err
        task_id = await task_queue.submit(req_dict, body.media_type, str(req.base_url))
        if not task_id:
            return error_response(503, "排队任务过多，请稍后再试")
        view = task_queue.task_view(task_id)
        view["status_url"] = f"{req.base_url}task_status/{task_id}"
        logger.info("TTS POST ip=%s task=%s 排队位置=%d 队列=%d",
                    ip, task_id, view["queue_position"], view["queue_length"])
        return JSONResponse(status_code=202, content=view)
    finally:
        elapsed = time.time() - t0
        text = req_dict.get("text", "")[:60]
        mode = "stream" if is_stream else "async"
        logger.info("TTS POST ip=%s %s mode=%s text=\"%s\" took=%.1fs", ip, mode, req_dict.get("text_lang", "?"), text, elapsed)


@router.get("/task_status/{task_id}", summary="查询合成任务状态（轮询）",
            description="轮询 POST /tts 提交的异步任务：`queued` 排队中（含排队位置与预计秒数）、"
                        "`running` 正在合成、`done` 已完成（返回 play_url/download_url）、"
                        "`error` 合成失败（返回 error）。任务状态保留 1 小时。",
            response_description="任务状态信息（含排队位置、预计秒数与完成后的播放/下载链接）",
            response_model=TTSTaskInfo, tags=["语音合成"])
async def task_status(task_id: str):
    view = task_queue.task_view(task_id)
    if view is None:
        return error_response(404, "任务不存在或已过期（任务状态保留 1 小时）")
    return view


@router.get("/audio/{filename}", include_in_schema=False)
async def serve_audio(filename: str, token: str = None):
    if config.AUDIO_AUTH_TOKEN and token != config.AUDIO_AUTH_TOKEN:
        return error_response(403, "缺少或无效的鉴权令牌")
    filepath = safe_audio_path(filename)
    if not filepath or not os.path.isfile(filepath):
        return error_response(404, "音频文件不存在或已过期（临时文件保留1小时）")
    ext = filename.rsplit(".", 1)[-1]
    mime = {"wav": "audio/wav", "ogg": "audio/ogg", "aac": "audio/aac"}.get(ext, "application/octet-stream")
    file_size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        data = f.read()
    # 只保留最普通的媒体响应头（Content-Type + Content-Length）：
    # - 不带 Content-Disposition：部分安卓 OEM 浏览器的下载管理器会拦截带该头的
    #   媒体请求导致 <audio> 加载失败（「点播放没反应」）；
    # - 不声明 Accept-Ranges：避免 WebView 走 Range/206 流程（其媒体栈处理 206
    #   时经常卡住）。下载按钮由前端 <a download> 属性实现，无需服务端 disposition。
    return Response(data, media_type=mime,
                    headers={"Content-Length": str(file_size)})


@router.get("/play", summary="在浏览器中直接播放合成的语音",
            description="浏览器在线试听：跳转到音频直链，由浏览器原生播放器播放。参数与 GET /tts 相同，适合交互式调试。流式模式下直接返回音频流。",
            response_description="302 跳转到音频直链（浏览器原生播放）；流式模式返回音频二进制流", tags=["语音合成"])
async def tts_play(request: Request,
    text: str, text_lang: str, ref_audio_path: str = config.DEFAULT_REF_AUDIO_PATH, prompt_lang: str = config.DEFAULT_PROMPT_LANG,
    prompt_text: str = config.DEFAULT_PROMPT_TEXT, aux_ref_audio_paths: str = None,
    gpt_path: str = "", sovits_path: str = "",
    top_k: int = 15, top_p: float = 1, temperature: float = 0.6,
    text_split_method: str = "cut0", batch_size: int = 20,
    batch_threshold: float = 0.75, split_bucket: bool = True,
    speed_factor: float = 1.0, fragment_interval: float = 0.3,
    seed: int = -1, media_type: str = "wav", parallel_infer: bool = True,
    repetition_penalty: float = 1.35, sample_steps: int = 32,
    super_sampling: bool = False, streaming_mode: Union[bool, int] = False,
    overlap_length: int = 2, min_chunk_length: int = 16,
):
    req = {
        "text": text, "text_lang": text_lang.lower(),
        "ref_audio_path": ref_audio_path,
        "prompt_text": prompt_text, "prompt_lang": prompt_lang.lower(),
        "top_k": int(top_k), "top_p": float(top_p),
        "temperature": float(temperature),
        "text_split_method": text_split_method,
        "batch_size": int(batch_size),
        "batch_threshold": float(batch_threshold),
        "split_bucket": split_bucket,
        "speed_factor": float(speed_factor),
        "fragment_interval": float(fragment_interval),
        "seed": int(seed), "media_type": media_type,
        "parallel_infer": parallel_infer,
        "repetition_penalty": float(repetition_penalty),
        "sample_steps": int(sample_steps),
        "super_sampling": super_sampling,
        "streaming_mode": streaming_mode,
        "overlap_length": int(overlap_length),
        "min_chunk_length": int(min_chunk_length),
        "aux_ref_audio_paths":
            aux_ref_audio_paths.split(",") if aux_ref_audio_paths else None,
        "gpt_path": gpt_path,
        "sovits_path": sovits_path,
    }
    is_stream = req.get("streaming_mode", False) or req.get("return_fragment", False)
    if is_stream:
        if not models_ready():
            return error_response(400, "模型未加载，请先在页面选择音色并点击「加载音色」")
        err = validate_tts_params(req)
        if err:
            return err
        err = resolve_streaming_params(req)
        if err:
            return err
        mime = media_type if media_type != "aac" else "aac"
        return StreamingResponse(
            _stream_audio_generator(req),
            media_type=f"audio/{mime}",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "Content-Disposition": "inline"},
        )
    result, err = await generate_audio_data(req)
    if err:
        return err
    sr, audio_data = result
    ext = "wav" if media_type == "wav" else media_type
    audio_bytes = pack_audio(BytesIO(), audio_data, sr, media_type).getvalue()
    file_id = uuid.uuid4().hex
    filename = f"{file_id}.{ext}"
    filepath = os.path.join(paths.TEMP_AUDIO_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(audio_bytes)
    return RedirectResponse(url=f"{request.base_url}audio/{filename}?token={config.AUDIO_AUTH_TOKEN}" if config.AUDIO_AUTH_TOKEN else f"{request.base_url}audio/{filename}")


@router.get("/debug_latest_audio", include_in_schema=False)
async def debug_latest_audio():
    """手机音频诊断页用：返回 temp_audio 中最新的一条音频文件名。"""
    files = [f for f in os.listdir(paths.TEMP_AUDIO_DIR)
             if f.lower().endswith((".wav", ".ogg", ".aac"))
             and os.path.isfile(os.path.join(paths.TEMP_AUDIO_DIR, f))]
    if not files:
        return {"name": None}
    files.sort(key=lambda f: os.path.getmtime(os.path.join(paths.TEMP_AUDIO_DIR, f)),
               reverse=True)
    return {"name": files[0]}


@router.get("/debug_audio", include_in_schema=False)
async def debug_audio_page():
    """手机音频诊断页：定位移动端「合成成功但无法播放」问题用。"""
    fp = os.path.join(paths.FRONTEND_DIR, "debug_audio.html")
    if os.path.isfile(fp):
        return FileResponse(fp, headers={"Cache-Control": "no-cache"})
    return Response(status_code=404)


@router.get("/disclaimer", include_in_schema=False)
async def disclaimer_page():
    """免责声明与注意事项页面（前台页脚入口）。"""
    fp = os.path.join(paths.FRONTEND_DIR, "disclaimer.html")
    if os.path.isfile(fp):
        return FileResponse(fp, headers={"Cache-Control": "no-cache"})
    return Response(status_code=404)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response()


@router.get("/", include_in_schema=False)
async def studio_home():
    """语音合成台前台页面：音色自选 + 一键合成"""
    if os.path.isfile(paths.STUDIO_HTML):
        return FileResponse(paths.STUDIO_HTML, headers={"Cache-Control": "no-cache"})
    return RedirectResponse(url="/docs")
