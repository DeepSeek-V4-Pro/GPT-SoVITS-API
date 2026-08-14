"""
系统端点
========
GET  /health    服务健康检查
GET  /models    扫描并列出全部可用音色、模型与参考音频
GET  /config    查看当前模型配置
POST /feedback  提交意见反馈
"""

import json
import os
import time

from fastapi import APIRouter, Request

from .. import paths
from ..logging_setup import logger
from ..engine import MODEL_CFG, models_ready, tts_config
from ..voice_library import scan_voice_library
from ..schemas import FeedbackRequest
from ..security import _get_client_ip
from ..validation import error_response

router = APIRouter()


@router.get("/health", summary="服务健康检查", response_description="返回服务运行状态和当前模型配置", tags=["系统"])
async def health():
    return {
        "status": "ok",
        "version": tts_config.version,
        "gpt_model": os.path.basename(MODEL_CFG["t2s_weights_path"]),
        "sovits_model": os.path.basename(MODEL_CFG["vits_weights_path"]),
        "encoder": os.path.basename(MODEL_CFG["cnhuhbert_base_path"]),
        "device": MODEL_CFG["device"],
        "languages": tts_config.languages,
        "loaded": models_ready(),
    }


@router.get("/models", summary="扫描并列出全部可用模型与参考音频",
            description="返回「语音合成台」前台使用的音色库：按音色分组的 GPT / SoVITS 模型和参考音频，以及当前加载的模型。",
            response_description="voices 按音色分组，gpt_models/sovits_models/ref_audios 为扁平列表", tags=["模型管理"])
async def list_models():
    data = scan_voice_library()
    data["current"] = {
        "gpt": MODEL_CFG["t2s_weights_path"],
        "sovits": MODEL_CFG["vits_weights_path"],
        "loaded": models_ready(),
    }
    return data


@router.post("/feedback", summary="提交意见反馈",
             description="提交使用建议、问题报告或新音色请求。反馈会以 JSONL 形式保存到服务端 "
                         "（feedback/feedback-YYYYMMDD.jsonl，位于项目目录下），供管理员查看。",
             response_description="成功返回 {结果: 成功, 提示: ...}，失败返回错误信息", tags=["反馈"])
async def submit_feedback(body: FeedbackRequest, request: Request):
    text = (body.text or "").strip()
    if not text:
        return error_response(400, "反馈内容不能为空")
    if len(text) > 2000:
        text = text[:2000]
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ip": _get_client_ip(request),
        "contact": (body.contact or "").strip()[:200],
        "page": (body.page or "").strip()[:300],
        "text": text,
    }
    try:
        with open(os.path.join(paths.FEEDBACK_DIR, time.strftime("feedback-%Y%m%d.jsonl")),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("写入反馈文件失败")
        return error_response(500, "反馈保存失败，请稍后重试")
    logger.info("收到反馈 ip=%s 长度=%d", record["ip"], len(text))
    return {"结果": "成功", "提示": "感谢你的反馈！管理员会尽快查看。"}


@router.get("/config", summary="查看当前加载的模型配置",
            response_description="当前配置的快照信息", tags=["系统"])
async def show_config():
    return {
        "gpt_model": os.path.basename(tts_config.t2s_weights_path),
        "sovits_model": os.path.basename(tts_config.vits_weights_path),
        "version": tts_config.version,
        "device": str(tts_config.device),
        "half_precision": tts_config.is_half,
    }
