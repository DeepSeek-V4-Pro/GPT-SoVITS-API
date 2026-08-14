"""
TTS 引擎初始化与模型切换协调
============================
- MODEL_CFG / tts_config / tts_pipeline: 与 GPT-SoVITS WebUI 一致的初始化流程
- ModelSwitchGuard: 音色代际守卫（合成与切换协调，切换排队语义）
- concurrency_semaphore: 全局合成并发限制
- models_ready: 判断当前模型是否已加载
"""

import asyncio
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from . import config, paths
from .logging_setup import logger
from .voice_library import scan_voice_library

from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
from GPT_SoVITS.TTS_infer_pack.text_segmentation_method import get_method_names

cut_method_names = get_method_names()


def _resolve_device(requested: str) -> str:
    """--device auto 时自动选择: 有 CUDA 用 cuda，否则回退 cpu；显式 cuda 但不可用时也回退。"""
    if requested not in ("auto", "cuda"):
        return requested
    try:
        import torch
    except Exception:
        return "cpu" if requested == "auto" else requested
    if torch.cuda.is_available():
        return "cuda"
    if requested == "cuda":
        logger.warning("--device cuda 但未检测到可用 GPU，已回退到 cpu")
    return "cpu"


_device = _resolve_device(config.DEVICE)

# 默认模型占位路径（voices/example/ 下无文件时，启动流程会自动改用扫描到的第一组可用模型）
MODEL_CFG = {
    "version": config.VERSION,
    "device": _device,
    "is_half": _device != "cpu",
    "t2s_weights_path": os.path.join(paths.VOICE_DIR, "example", "example-e15.ckpt"),
    "vits_weights_path": os.path.join(paths.VOICE_DIR, "example", "example_e8_s208.pth"),
    "bert_base_path": os.path.join(paths.NOW_DIR, "GPT_SoVITS", "pretrained_models",
                                   "chinese-roberta-wwm-ext-large"),
    "cnhuhbert_base_path": os.path.join(paths.NOW_DIR, "GPT_SoVITS", "pretrained_models",
                                        "chinese-hubert-base"),
}

# 启动时若默认模型文件不存在，自动改用 voices/ 中扫描到的第一组可用模型
if not (os.path.isfile(MODEL_CFG["t2s_weights_path"]) and os.path.isfile(MODEL_CFG["vits_weights_path"])):
    _boot_lib = scan_voice_library()
    for _v in _boot_lib["voices"]:
        if _v["gpt"] and _v["sovits"]:
            MODEL_CFG["t2s_weights_path"] = _v["gpt"][0]["path"]
            MODEL_CFG["vits_weights_path"] = _v["sovits"][0]["path"]
            logger.warning("默认模型不存在，已自动选用 voices/ 中扫描到的第一组模型: %s + %s",
                           MODEL_CFG["t2s_weights_path"], MODEL_CFG["vits_weights_path"])
            break

logger.info("=" * 50)
logger.info("GPT-SoVITS 语音合成 API 服务（语音合成台）")
logger.info("=" * 50)
for k, v in MODEL_CFG.items():
    logger.info("  %s: %s", k, v)
logger.info("-" * 50)

# 清理上次运行遗留的临时音频
os.makedirs(paths.TEMP_AUDIO_DIR, exist_ok=True)
removed_count = 0
for f in os.listdir(paths.TEMP_AUDIO_DIR):
    try:
        os.remove(os.path.join(paths.TEMP_AUDIO_DIR, f))
        removed_count += 1
    except OSError:
        pass
if removed_count:
    logger.info("清理旧临时文件: %d 个", removed_count)


class ModelSwitchGuard:
    """音色代际守卫 + 切换执行器（多人共用的"不打架"协调核心）。

    核心思想：每条合成任务在**提交时刻**绑定音色代际（epoch），
    音色切换排在**全部旧代际任务（含排队中的）**之后生效；
    切换期间新提交的任务属于新代际，等切换完成后才开始合成。

    升级（多用户各自选音色）：任务可以声明**目标音色**（admit_for_voice），
    服务端保证该任务最终用自己声明的音色合成；若有人中途切换/合并覆盖了目标，
    任务会自动再排一次「切回自己音色」的切换，绝不拿错音色。

    - admit():              任务提交时登记（跟随当前/待切换音色），返回代际凭据 holder
    - admit_for_voice():    任务按目标音色登记（原子，无竞态），返回代际凭据 holder
    - release(holder):      任务结束（无论成败）时注销
    - synthesis(holder):    任务等自己的音色模型加载完成后再合成；不符则自动补排切换
    - request_switch(...):  注册切换请求（异步排队语义，last-writer-wins 合并）
    - status():             供 /switch_status 展示当前/待切换/剩余任务
    - start()/stop():       切换执行器后台任务（app lifespan 中启停）

    保证：
    1. B 已提交（含排队）的任务一定用提交时的音色跑完；
    2. A 的切换永远发生在这些任务全部结束之后；
    3. 切换请求排队期间再来新请求 → 合并覆盖，不多次加载模型；
    4. 声明了目标音色的任务，无论期间发生多少次别人的切换/合并，最终都用自己的
       音色合成（必要时自动追加一次切回切换）；
    5. 来回切换、多人切换的最终状态对所有人可见（status）。
    """

    def __init__(self):
        self._cond = asyncio.Condition()
        self._loaded_epoch = 0                # 当前已加载模型的代际
        self._pending = None                  # 待执行切换 {epoch,target,operator,force,phase,...}
        self._holders = defaultdict(dict)     # epoch -> {id(holder): 该代际未完成任务凭据（含排队）}
        self._active = defaultdict(int)       # epoch -> 正在合成的任务数
        self._worker_task = None
        self._last_done_at = 0.0
        self._last_result = None              # 最近一次切换结果（含失败原因）

    # ---------- 任务代际登记 ----------

    async def admit(self):
        """任务提交时调用：返回该任务的代际凭据。
        无待切换 → 当前代际；已有人在排队切换 → 新代际（等切换完成后才能合成）。"""
        async with self._cond:
            epoch = self._pending["epoch"] if self._pending else self._loaded_epoch
            holder = {"epoch": epoch, "voice": None, "voice_error": None}
            self._holders[epoch][id(holder)] = holder
            return holder

    async def admit_for_voice(self, sovits_path=None, gpt_path=None, operator=""):
        """任务提交时按**目标音色**登记（原子操作，无竞态）。

        每条任务都可以声明自己想用的音色：
        - 目标与当前已加载一致 → 绑定当前代际，直接排队合成；
        - 已有切换在排队且目标一致 → 绑定该切换代际；
        - 已有切换在排队但目标不同 → 先绑到该代际，等它加载后自动再排一次切回本音色；
        - 无排队切换 → 自动并入切换队列（生成一个新代际）。

        无论中途发生多少次别人的切换，本任务都保证最终用自己声明的音色合成。
        启用 SWITCH_AUTH_TOKEN 时不做自动切换（避免绕过切换鉴权），退回 admit() 语义。
        """
        if config.SWITCH_AUTH_TOKEN and (sovits_path or gpt_path):
            return await self.admit()
        async with self._cond:
            has_voice = bool(sovits_path or gpt_path)
            if not has_voice:
                epoch = self._pending["epoch"] if self._pending else self._loaded_epoch
                holder = {"epoch": epoch, "voice": None, "voice_error": None}
            else:
                target = self._current_target_locked()
                if gpt_path:
                    target["gpt"] = gpt_path
                if sovits_path:
                    target["sovits"] = sovits_path
                if self._target_same_locked(target):
                    epoch = self._loaded_epoch
                elif self._pending:
                    epoch = self._pending["epoch"]
                else:
                    entry = self._try_create_pending_locked(target, operator=operator)
                    if entry is None:
                        holder = {"epoch": self._loaded_epoch, "voice": target,
                                  "voice_error": "音色切换过于频繁，请稍后再试"}
                        self._holders[holder["epoch"]][id(holder)] = holder
                        self._cond.notify_all()
                        return holder
                    epoch = entry["epoch"]
                holder = {"epoch": epoch, "voice": target, "voice_error": None}
            self._holders[epoch][id(holder)] = holder
            return holder

    async def release(self, holder):
        """任务结束时调用（含排队中的任务被清理、合成失败等一切出口）。"""
        async with self._cond:
            ep = holder["epoch"]
            self._holders[ep].pop(id(holder), None)
            if not self._holders[ep]:
                self._holders.pop(ep, None)
            self._cond.notify_all()

    def current_epoch(self):
        """当前已加载模型的代际（任务状态提示用，读整型属性天然原子）。"""
        return self._loaded_epoch

    # ---------- 合成互斥 ----------

    @asynccontextmanager
    async def synthesis(self, holder):
        """任务进入合成：等自己的音色模型加载完成后放行；合成期间切换会让路。

        若任务声明了音色而当前加载的不是该音色（切换被他人合并覆盖、失败等），
        自动把任务重新排到「切回自己音色」的切换之后，绝不拿错音色合成。
        """
        async with self._cond:
            while True:
                if holder.get("voice_error"):
                    break
                if holder["epoch"] != self._loaded_epoch:
                    await self._cond.wait()
                    continue
                voice = holder.get("voice")
                if voice is None or self._targets_equal(voice, self._current_target_locked()):
                    self._active[holder["epoch"]] += 1
                    break
                # 本代际加载的音色与任务要求不符 → 需要再排一次切换
                if self._pending:
                    # 已有切换在排队：先挪到它的代际等待（避免阻塞它排空），加载后重新判定
                    self._holders[holder["epoch"]].pop(id(holder), None)
                    holder["epoch"] = self._pending["epoch"]
                    self._holders[holder["epoch"]][id(holder)] = holder
                    await self._cond.wait()
                    continue
                entry = self._try_create_pending_locked(voice)
                if entry is None:
                    holder["voice_error"] = "音色切换过于频繁，请稍后再试"
                    continue
                self._holders[holder["epoch"]].pop(id(holder), None)
                holder["epoch"] = entry["epoch"]
                self._holders[holder["epoch"]][id(holder)] = holder
                self._cond.notify_all()
            if holder.get("voice_error"):
                raise RuntimeError(holder["voice_error"])
        try:
            yield
        finally:
            async with self._cond:
                ep = holder["epoch"]
                self._active[ep] = max(0, self._active[ep] - 1)
                self._holders[ep].pop(id(holder), None)
                if not self._holders[ep]:
                    self._holders.pop(ep, None)
                self._cond.notify_all()

    # ---------- 切换请求 ----------

    async def request_switch(self, sovits_path=None, gpt_path=None,
                             operator="", force=False, token=""):
        """注册音色切换请求。返回 {status: queued/noop/rejected/denied/error, ...}"""
        if config.SWITCH_AUTH_TOKEN and token != config.SWITCH_AUTH_TOKEN:
            return {"status": "denied"}
        if not sovits_path and not gpt_path:
            return {"status": "error", "错误": "缺少模型路径参数"}
        now = time.time()
        async with self._cond:
            target = self._current_target_locked()
            if sovits_path:
                target["sovits"] = sovits_path
            if gpt_path:
                target["gpt"] = gpt_path
            if self._pending:
                # 已有切换在排队：合并覆盖（last-writer-wins），不多加载一次模型
                p = self._pending
                if sovits_path:
                    p["target"]["sovits"] = sovits_path
                if gpt_path:
                    p["target"]["gpt"] = gpt_path
                p["operator"] = operator or p["operator"]
                p["requested_at"] = now
                p["force"] = p["force"] or force
                if self._target_same_locked(p["target"]):
                    self._cancel_pending_locked()
                    self._cond.notify_all()
                    return {"status": "noop", "cancelled": True}
                self._cond.notify_all()
                return self._queued_view_locked(merged=True)
            # 无待切换
            if self._target_same_locked(target):
                return {"status": "noop"}
            if (config.SWITCH_MIN_INTERVAL and self._last_done_at
                    and now - self._last_done_at < config.SWITCH_MIN_INTERVAL):
                return {"status": "rejected",
                        "retry_after": int(config.SWITCH_MIN_INTERVAL - (now - self._last_done_at)) + 1}
            self._pending = {
                "epoch": self._loaded_epoch + 1,
                "target": target,
                "operator": operator,
                "requested_at": now,
                "force": bool(force),
                "phase": "draining",   # draining → loading
                "started_at": None,
            }
            self._cond.notify_all()
            return self._queued_view_locked(merged=False)

    def _queued_view_locked(self, merged):
        p = self._pending
        return {
            "status": "queued",
            "merged": merged,
            "epoch": p["epoch"],
            "operator": p["operator"],
            "force": p["force"],
            "target": dict(p["target"]),
            "drain": {
                "remaining": len(self._holders.get(self._loaded_epoch, ())),
                "running": self._active.get(self._loaded_epoch, 0),
                "waiting_new": len(self._holders.get(p["epoch"], ())),
            },
        }

    @staticmethod
    def _targets_equal(a, b):
        return bool(a and b and a.get("sovits") == b.get("sovits")
                    and a.get("gpt") == b.get("gpt"))

    def _try_create_pending_locked(self, target, operator="", force=False):
        """创建待切换记录；目标与当前一致或触发限频时返回 None。"""
        if self._target_same_locked(target):
            return None
        now = time.time()
        if (config.SWITCH_MIN_INTERVAL and self._last_done_at
                and now - self._last_done_at < config.SWITCH_MIN_INTERVAL):
            return None
        self._pending = {
            "epoch": self._loaded_epoch + 1,
            "target": target,
            "operator": (operator or "").strip()[:40],
            "requested_at": now,
            "force": bool(force),
            "phase": "draining",
            "started_at": None,
        }
        self._cond.notify_all()
        return self._pending

    def _rebind_holder_locked(self, holder):
        """切换被取消/失败时，把等待中的任务按各自音色重新安置。"""
        voice = holder.get("voice")
        if voice is None or self._target_same_locked(voice):
            self._holders[self._loaded_epoch][id(holder)] = holder
            holder["epoch"] = self._loaded_epoch
            return
        if self._pending and self._targets_equal(self._pending["target"], voice):
            self._holders[self._pending["epoch"]][id(holder)] = holder
            holder["epoch"] = self._pending["epoch"]
            return
        entry = self._try_create_pending_locked(voice)
        if entry is None:
            holder["epoch"] = self._loaded_epoch
            holder["voice_error"] = "音色切换过于频繁，请稍后再试"
            self._holders[self._loaded_epoch][id(holder)] = holder
            return
        self._holders[entry["epoch"]][id(holder)] = holder
        holder["epoch"] = entry["epoch"]

    def _cancel_pending_locked(self):
        """取消待切换（目标与当前一致时），等待中的任务按各自音色重新安置。"""
        p = self._pending
        if p is None:
            return
        self._pending = None
        hs = self._holders.pop(p["epoch"], {})
        for h in hs.values():
            self._rebind_holder_locked(h)
        self._last_result = {"status": "cancelled", "target": dict(p["target"]),
                             "operator": p["operator"], "done_at": time.time()}
        logger.info("待切换已取消（目标与当前音色一致）")

    def _demote_locked(self, from_epoch, to_epoch):
        """把 from_epoch 的全部未完成任务凭据转移到 to_epoch（切换取消/失败时用）。"""
        if from_epoch == to_epoch:
            return
        hs = self._holders.pop(from_epoch, None)
        if not hs:
            return
        for h in hs.values():
            h["epoch"] = to_epoch
        self._holders[to_epoch].update(hs)
        if self._active.get(from_epoch):
            self._active[to_epoch] += self._active.pop(from_epoch, 0)

    def _current_target_locked(self):
        return {"sovits": MODEL_CFG["vits_weights_path"],
                "gpt": MODEL_CFG["t2s_weights_path"]}

    def _target_same_locked(self, target):
        return self._targets_equal(target, self._current_target_locked())

    # ---------- 切换执行器 ----------

    async def start(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._switch_worker())
            logger.info("音色切换执行器已启动")

    async def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def _switch_worker(self):
        while True:
            async with self._cond:
                while self._pending is None:
                    await self._cond.wait()
                pending_epoch = self._pending["epoch"]
            # 等待旧代际任务（含排队）全部跑完；强制模式只等正在运行的
            while True:
                async with self._cond:
                    p = self._pending
                    if p is None or p["epoch"] != pending_epoch:
                        break  # 期间被取消 → 回到外层重新等待
                    if p["force"]:
                        ready = self._active.get(self._loaded_epoch, 0) == 0
                    else:
                        ready = not self._holders.get(self._loaded_epoch)
                    if ready:
                        break
                    p["phase"] = "draining"
                    await self._cond.wait()
            async with self._cond:
                p = self._pending
                if p is None or p["epoch"] != pending_epoch:
                    continue  # 已被取消
                target = dict(p["target"])
                force = p["force"]
                if force:
                    # 强制：排队中未开始的旧代际任务改随新音色
                    self._demote_locked(self._loaded_epoch, p["epoch"])
                p["phase"] = "loading"
                p["started_at"] = time.time()
            ok, err = await self._apply_models(target)
            async with self._cond:
                p = self._pending
                if p and p["epoch"] == pending_epoch:
                    if p["target"] != target or p["force"] != force:
                        # 加载期间目标又被修改 → 不结束本次，重新排空再加载
                        continue
                    self._pending = None
                    if ok:
                        self._loaded_epoch = p["epoch"]
                        self._last_done_at = time.time()
                        self._last_result = {"status": "done", "target": target,
                                             "operator": p["operator"], "done_at": self._last_done_at}
                        logger.info("音色切换完成: gpt=%s sovits=%s", target["gpt"], target["sovits"])
                    else:
                        # 失败：等待该切换的任务按各自音色重新安置；
                        # 目标音色本身的任务标记失败（避免无限重试加载）
                        hs = self._holders.pop(p["epoch"], {})
                        for h in hs.values():
                            if h.get("voice") is not None and self._targets_equal(h["voice"], p["target"]):
                                h["epoch"] = self._loaded_epoch
                                h["voice_error"] = err or "模型加载失败"
                                self._holders[self._loaded_epoch][id(h)] = h
                            else:
                                self._rebind_holder_locked(h)
                        self._last_result = {"status": "failed", "error": err, "target": target,
                                             "operator": p["operator"], "done_at": time.time()}
                        logger.error("音色切换失败: %s", err)
                    self._cond.notify_all()
                elif p is None:
                    # 等待加载期间被取消（目标改回当前音色），丢弃本次加载结果
                    logger.info("切换加载完成但目标已变更，丢弃: %s", target)

    async def _apply_models(self, target):
        """在线程中执行权重加载（加载耗时数秒，避免阻塞事件循环）。
        此刻已无旧代际任务触碰 tts_pipeline，新代际任务都在等 epoch 变更。"""
        try:
            await asyncio.to_thread(self._apply_models_sync, target)
            return True, None
        except Exception as e:
            logger.exception("模型权重加载异常")
            return False, str(e)

    def _apply_models_sync(self, target):
        if target["sovits"] != MODEL_CFG["vits_weights_path"]:
            tts_pipeline.init_vits_weights(target["sovits"])
            MODEL_CFG["vits_weights_path"] = target["sovits"]
        if target["gpt"] != MODEL_CFG["t2s_weights_path"]:
            tts_pipeline.init_t2s_weights(target["gpt"])
            MODEL_CFG["t2s_weights_path"] = target["gpt"]

    # ---------- 状态 ----------

    async def status(self):
        """当前/待切换状态快照（供 /switch_status）。"""
        async with self._cond:
            out = {
                "current": self._current_target_locked(),
                "loaded_epoch": self._loaded_epoch,
                "pending": None,
                "last_result": dict(self._last_result) if self._last_result else None,
                "reserved": {str(ep): len(hs) for ep, hs in self._holders.items() if hs},
                "active": {str(ep): n for ep, n in self._active.items() if n},
            }
            p = self._pending
            if p:
                out["pending"] = {
                    "epoch": p["epoch"],
                    "target": dict(p["target"]),
                    "operator": p["operator"],
                    "requested_at": p["requested_at"],
                    "force": p["force"],
                    "phase": p["phase"],
                    "started_at": p["started_at"],
                    "drain": {
                        "remaining": len(self._holders.get(self._loaded_epoch, ())),
                        "running": self._active.get(self._loaded_epoch, 0),
                        "waiting_new": len(self._holders.get(p["epoch"], ())),
                    },
                }
            return out

    async def stats(self):
        """兼容旧调用：active=正在合成的任务数, switching=是否有切换进行/排队"""
        st = await self.status()
        return {
            "active": sum(int(v) for v in st["active"].values()),
            "switching": st["pending"] is not None,
        }


model_switch_guard = ModelSwitchGuard()
concurrency_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT)


async def admit_for_req(req: dict):
    """按请求声明的音色登记合成任务（无声明时退回「跟随当前音色」语义）。"""
    return await model_switch_guard.admit_for_voice(
        sovits_path=req.get("sovits_path") or None,
        gpt_path=req.get("gpt_path") or None,
    )

# 与 WebUI 完全一致的初始化顺序
tts_config = TTS_Config(os.path.join(paths.NOW_DIR, "GPT_SoVITS", "configs", "tts_infer.yaml"))
tts_config.device = MODEL_CFG["device"]
tts_config.is_half = MODEL_CFG["is_half"]
tts_config.update_version(MODEL_CFG["version"])
tts_config.t2s_weights_path = MODEL_CFG["t2s_weights_path"]
tts_config.vits_weights_path = MODEL_CFG["vits_weights_path"]
tts_config.cnhuhbert_base_path = MODEL_CFG["cnhuhbert_base_path"]
tts_config.bert_base_path = MODEL_CFG["bert_base_path"]
logger.info("TTS 配置: %s", tts_config)


# 模型文件缺失时也能启动服务（等待用户在前台选择可用模型），而不是直接崩溃
def _resilient_init_models(self):
    if os.path.isfile(self.configs.t2s_weights_path):
        self.init_t2s_weights(self.configs.t2s_weights_path)
    else:
        logger.warning("GPT 模型文件缺失，跳过加载: %s", self.configs.t2s_weights_path)
    if os.path.isfile(self.configs.vits_weights_path):
        self.init_vits_weights(self.configs.vits_weights_path)
    else:
        logger.warning("SoVITS 模型文件缺失，跳过加载: %s", self.configs.vits_weights_path)
    self.init_bert_weights(self.configs.bert_base_path)
    self.init_cnhuhbert_weights(self.configs.cnhuhbert_base_path)


TTS._init_models = _resilient_init_models
tts_pipeline = TTS(tts_config)
logger.info("-" * 50)
logger.info("支持语言: %s", tts_config.languages)
logger.info("=" * 50)


def models_ready() -> bool:
    """GPT 与 SoVITS 模型是否都已加载完成"""
    return (
        getattr(tts_pipeline, "t2s_model", None) is not None
        and getattr(tts_pipeline, "vits_model", None) is not None
    )
