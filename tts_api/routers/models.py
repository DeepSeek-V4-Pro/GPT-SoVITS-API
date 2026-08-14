"""
模型管理端点
============
GET /set_gpt_weights     热切换 GPT 模型（异步排队，202 受理）
GET /set_sovits_weights  热切换 SoVITS 模型（异步排队，202 受理）
GET /set_voice           一次性切换 SoVITS+GPT（前台「加载音色」使用）
GET /switch_status       当前音色 / 待切换状态（前端横幅轮询）

多人共用协调语义：
- 切换会等待「切换请求提交前」的全部任务（含排队中的）跑完才生效，
  不会把别人排队中的任务换成新音色；
- 切换排队期间的新任务按新音色等待（提交时刻绑定音色代际）；
- 切换排队期间再来新请求 → 合并覆盖（last-writer-wins），不多加载模型；
- 目标与当前音色一致时直接取消/返回成功；
- 可选 operator 昵称记录操作人，/switch_status 对所有人可见。
"""

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import config
from ..logging_setup import logger
from ..engine import MODEL_CFG, model_switch_guard
from ..tasks import task_queue
from ..validation import error_response
from ..voice_library import guess_speaker, is_allowed_model_path

router = APIRouter()


def _voice_name(path):
    return guess_speaker(os.path.basename(path or ""))


def _eta_seconds(remaining):
    """按平均任务耗时估算排空剩余任务需要的秒数。"""
    if remaining <= 0:
        return 0
    avg = task_queue.avg_duration()
    return int(round(avg * ((remaining + config.MAX_CONCURRENT - 1) // config.MAX_CONCURRENT)))


async def _do_switch(sovits_path, gpt_path, operator, force, token, kind):
    """统一处理切换请求：校验 → 注册 → 返回 202/成功/错误。"""
    result = await model_switch_guard.request_switch(
        sovits_path=sovits_path, gpt_path=gpt_path,
        operator=(operator or "").strip()[:40], force=bool(force), token=token or "")
    status = result["status"]
    if status == "denied":
        return error_response(403, "缺少或无效的切换令牌")
    if status == "rejected":
        return error_response(429, "切换过于频繁，请 %d 秒后再试" % result.get("retry_after", 30))
    if status == "error":
        return error_response(400, result.get("错误", "切换请求无效"))
    if status == "noop":
        return {"结果": "成功", "说明": "已是当前音色，无需切换",
                "模型路径": sovits_path or gpt_path}
    # queued → 202 受理
    drain = result["drain"]
    remaining = drain["remaining"]
    op = result["operator"]
    payload = {
        "结果": "已受理",
        "说明": (f"切换已排队：等待前方 {remaining} 个任务完成后生效"
                 if remaining else "切换即将开始（无排队任务）"),
        "operator": op,
        "merged": result["merged"],
        "force": result["force"],
        "drain_remaining": remaining,
        "drain_running": drain["running"],
        "eta_seconds": _eta_seconds(remaining),
        "当前音色": {"gpt": MODEL_CFG["t2s_weights_path"], "sovits": MODEL_CFG["vits_weights_path"]},
        "目标音色": result["target"],
    }
    logger.info("%s 切换已排队 operator=%s 前方任务=%d 预计=%ds merged=%s force=%s",
                kind, op or "-", remaining, payload["eta_seconds"],
                result["merged"], result["force"])
    return JSONResponse(status_code=202, content=payload)


@router.get("/set_gpt_weights", summary="热切换 GPT 模型（异步排队）",
            description="切换 GPT(Text2Semantic) 模型。路径需来自 /models 扫描结果。"
                        "切换会等待请求提交前已排队的全部合成任务结束后执行（202 受理），"
                        "期间新提交的合成任务将使用新音色。轮询 GET /switch_status 查看进度。",
            response_description="202: 切换已排队（含剩余任务数与预计秒数）；目标已是当前音色时返回 {结果: 成功}",
            tags=["模型管理"])
async def set_gpt_weights(weights_path: str, operator: str = "", force: bool = False, token: str = ""):
    weights_path = (weights_path or "").strip()
    if not weights_path:
        return error_response(400, "缺少参数: weights_path")
    if not is_allowed_model_path(weights_path):
        return error_response(400, "非法的模型路径")
    return await _do_switch(None, weights_path, operator, force, token, "GPT")


@router.get("/set_sovits_weights", summary="热切换 SoVITS 模型（异步排队）",
            description="切换 SoVITS(声码器) 模型。路径需来自 /models 扫描结果。"
                        "切换会等待请求提交前已排队的全部合成任务结束后执行（202 受理），"
                        "期间新提交的合成任务将使用新音色。轮询 GET /switch_status 查看进度。",
            response_description="202: 切换已排队（含剩余任务数与预计秒数）；目标已是当前音色时返回 {结果: 成功}",
            tags=["模型管理"])
async def set_sovits_weights(weights_path: str, operator: str = "", force: bool = False, token: str = ""):
    weights_path = (weights_path or "").strip()
    if not weights_path:
        return error_response(400, "缺少参数: weights_path")
    if not is_allowed_model_path(weights_path):
        return error_response(400, "非法的模型路径")
    return await _do_switch(weights_path, None, operator, force, token, "SoVITS")


@router.get("/set_voice", summary="切换音色（SoVITS + GPT 一次完成，推荐）",
            description="一次性切换 SoVITS 与 GPT 模型（前台「加载音色」按钮使用）。"
                        "参数可只给其一；切换排队期间新请求会合并覆盖旧目标，"
                        "最终只加载一次模型。force=1 为紧急切换：只等正在合成的任务，"
                        "排队中的任务将改用新音色（谨慎使用）。",
            response_description="202: 切换已排队（含剩余任务数与预计秒数）；目标已是当前音色时返回 {结果: 成功}",
            tags=["模型管理"])
async def set_voice(sovits: str = "", gpt: str = "", operator: str = "",
                    force: bool = False, token: str = ""):
    sovits = (sovits or "").strip()
    gpt = (gpt or "").strip()
    if not sovits and not gpt:
        return error_response(400, "缺少参数: 至少提供 sovits 或 gpt 模型路径")
    for p in (sovits, gpt):
        if p and not is_allowed_model_path(p):
            return error_response(400, "非法的模型路径: " + p)
    return await _do_switch(sovits or None, gpt or None, operator, force, token, "音色")


@router.get("/switch_status", summary="音色切换状态（横幅轮询）",
            description="返回当前加载音色、待切换请求（操作人/目标/剩余任务/预计秒数/阶段）"
                        "与最近一次切换结果。多人共用时前端横幅据此展示，避免切换互相冲突。",
            response_description="current=当前音色; pending=待切换(null 表示无); last_result=最近一次切换结果",
            tags=["模型管理"])
async def switch_status():
    st = await model_switch_guard.status()
    cur = st["current"]
    out = {
        "current": {
            "gpt": cur["gpt"], "sovits": cur["sovits"],
            "gpt_name": _voice_name(cur["gpt"]), "sovits_name": _voice_name(cur["sovits"]),
            "loaded_epoch": st["loaded_epoch"],
        },
        "pending": None,
        "last_result": st["last_result"],
        "reserved": st["reserved"],
        "active": st["active"],
    }
    p = st["pending"]
    if p:
        t = p["target"]
        d = p["drain"]
        out["pending"] = {
            "epoch": p["epoch"],
            "phase": p["phase"],  # draining=等待旧任务 / loading=加载权重中
            "operator": p["operator"],
            "force": p["force"],
            "requested_at": p["requested_at"],
            "started_at": p["started_at"],
            "target": {
                "gpt": t["gpt"], "sovits": t["sovits"],
                "gpt_name": _voice_name(t["gpt"]), "sovits_name": _voice_name(t["sovits"]),
            },
            "drain": {
                "remaining": d["remaining"],
                "running": d["running"],
                "waiting_new": d["waiting_new"],
            },
            "eta_seconds": _eta_seconds(d["remaining"]),
        }
    return out
