"""
TTS 核心合成
============
- _synthesize: 受切换锁 + 并发信号量保护的完整合成
- _stream_audio_generator: 逐块产出编码字节的流式生成器
- generate_audio_data / handle_tts: 非流式合成与统一处理入口
- safe_audio_path: 临时音频文件路径安全校验
"""

import os
import time
from io import BytesIO

import numpy as np
from fastapi.responses import Response, StreamingResponse

from . import paths
from .logging_setup import logger
from .audio import pack_audio, _wav_stream_header
from .engine import (
    tts_pipeline,
    model_switch_guard,
    concurrency_semaphore,
    models_ready,
    admit_for_req,
)
from .security import send_alert
from .validation import error_response, resolve_streaming_params, validate_tts_params


async def _stream_audio_generator(req: dict):
    """异步生成器：逐块产出编码后的音频字节，用于 StreamingResponse。

    流式开始前向守卫登记音色代际（holder）：流式期间持续占用一个并发槽位，
    音色切换会等待流结束；若已有人在排队切换，本流等新音色加载后再开始。
    """
    media_type = req.get("media_type", "wav")
    holder = await admit_for_req(req)
    try:
        async with model_switch_guard.synthesis(holder):
            async with concurrency_semaphore:
                tts_gen = tts_pipeline.run(req)
                first = True
                for sr, chunk in tts_gen:
                    if first:
                        if media_type == "wav":
                            yield _wav_stream_header(sr)
                        first = False
                    if media_type in ("wav", "raw"):
                        yield chunk.tobytes()
                    else:
                        buf = BytesIO()
                        pack_audio(buf, chunk, sr, media_type)
                        yield buf.getvalue()
    except Exception:
        logger.exception("流式音频生成异常")
        send_alert(f"流式生成异常: text={req.get('text','')[:40]}")
    finally:
        await model_switch_guard.release(holder)


async def _synthesize(req: dict, holder: dict):
    """受音色代际守卫 + 并发信号量保护的完整合成，返回 (sr, audio_data)。

    holder 由调用方在请求进入时 admit()、结束后 release()：
    合成期间音色切换会等待本任务结束；若切换已排队，本任务等新音色加载后
    才按自己的代际开始（提交时绑定音色）。
    """
    async with model_switch_guard.synthesis(holder):
        async with concurrency_semaphore:
            tts_gen = tts_pipeline.run(req)
            all_audio = []
            sr = None
            for sr, chunk in tts_gen:
                all_audio.append(chunk)
            if not all_audio or sr is None:
                # 文本切分后为空等情况下引擎可能不产出任何音频
                raise ValueError("引擎未产出音频数据，请检查文本与切分参数")
            audio_data = np.concatenate(all_audio, axis=-1) if len(all_audio) > 1 else all_audio[0]
            return sr, audio_data


async def generate_audio_data(req: dict):
    """非流式模式（GET /tts、GET /play）：同步合成完整音频后返回 (sr, audio_data)。"""
    if not models_ready():
        return None, error_response(400, "模型未加载，请先在页面选择音色并点击「加载音色」")
    err = validate_tts_params(req)
    if err:
        return None, err
    err = resolve_streaming_params(req)
    if err:
        return None, err
    holder = await admit_for_req(req)
    try:
        return await _synthesize(req, holder), None
    except Exception:
        logger.exception("语音合成异常")
        send_alert(f"语音合成异常: text={req.get('text','')[:40]}")
        return None, error_response(400, "语音合成失败，请检查参数或稍后重试")
    finally:
        await model_switch_guard.release(holder)


async def handle_tts(req: dict):
    media_type = req.get("media_type", "wav")
    is_stream = req.get("streaming_mode", False) or req.get("return_fragment", False)
    t0 = time.time()
    try:
        if is_stream:
            if not models_ready():
                return error_response(400, "模型未加载，请先在页面选择音色并点击「加载音色」")
            err = validate_tts_params(req)
            if err:
                return err
            err = resolve_streaming_params(req)
            if err:
                return err
            mime = media_type
            return StreamingResponse(
                _stream_audio_generator(req),
                media_type=f"audio/{mime}",
                headers={"Content-Disposition": f"attachment; filename=tts_output.{media_type}",
                         "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        result, err = await generate_audio_data(req)
        if err:
            return err
        sr, audio_data = result
        audio_bytes = pack_audio(BytesIO(), audio_data, sr, media_type).getvalue()
        return Response(audio_bytes, media_type=f"audio/{media_type}",
                        headers={"Content-Disposition": f"attachment; filename=tts_output.{media_type}"})
    finally:
        elapsed = time.time() - t0
        text = req.get("text", "")[:60]
        mode = "stream" if is_stream else "full"
        logger.info("TTS %s mode=%s text=\"%s\" took=%.1fs", mode, req.get("text_lang", "?"), text, elapsed)


def safe_audio_path(filename: str):
    """验证并返回安全的临时音频文件路径，不合法则返回 None"""
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return None
    filepath = os.path.join(paths.TEMP_AUDIO_DIR, filename)
    real_dir = os.path.realpath(paths.TEMP_AUDIO_DIR)
    real = os.path.realpath(filepath)
    if not real.startswith(real_dir + os.sep):
        return None
    return filepath
