"""
异步合成任务队列
================
POST /tts 提交后立即返回 202 与 task_id，后台 worker 按 FIFO 合成，
前端轮询 GET /task_status/{task_id} 获取排队位置 / 预计秒数 / 结果链接。

- 排队位置 = 正在运行的任务数 + 该任务在队列中的序号；
- 预计秒数根据最近 20 个任务的完成耗时估算（无历史时按 TASK_ETA_DEFAULT）。
"""

import asyncio
import os
import time
import uuid
from collections import deque
from io import BytesIO

from . import config, paths
from .logging_setup import logger
from .audio import pack_audio
from .engine import MODEL_CFG, model_switch_guard
from .security import send_alert
from .synth import _synthesize
from .voice_library import guess_speaker


class TaskQueue:
    """POST /tts 异步任务队列：提交即返回 202，后台 worker 按 FIFO 合成，可轮询状态。"""

    def __init__(self, max_workers=config.MAX_CONCURRENT, max_queue=config.MAX_QUEUE_SIZE):
        self.max_workers = max_workers
        self.max_queue = max_queue
        self._queue = asyncio.Queue()
        self._tasks = {}                     # task_id -> 任务字典
        self._order = []                     # 队列中任务 id 的提交顺序
        self._running = 0                    # 正在运行的任务数
        self._durations = deque(maxlen=20)   # 最近完成耗时（秒），用于 ETA 估算
        self._workers = []

    # ---------- 提交与查询 ----------

    async def submit(self, req, media_type, base_url):
        """校验通过后入队。返回 task_id；队列已满返回 None。

        提交时即向守卫登记音色代际（holder）：任务之后无论排队多久、
        期间发生多少次音色切换，都按提交时的音色合成；
        反过来，音色切换会等待本任务结束（含排队）后才生效。
        """
        if self._queue.qsize() >= self.max_queue:
            return None
        holder = await model_switch_guard.admit_for_voice(
            sovits_path=req.get("sovits_path") or None,
            gpt_path=req.get("gpt_path") or None)
        task_id = uuid.uuid4().hex
        gpt_path = req.get("gpt_path") or MODEL_CFG["t2s_weights_path"]
        sovits_path = req.get("sovits_path") or MODEL_CFG["vits_weights_path"]
        self._tasks[task_id] = {
            "id": task_id,
            "status": "queued",
            "req": req,
            "media_type": media_type,
            "base_url": base_url,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "elapsed": None,
            "error": None,
            "result": None,
            "holder": holder,
            "voice": {
                "gpt": gpt_path,
                "sovits": sovits_path,
                "gpt_name": guess_speaker(os.path.basename(gpt_path)),
                "sovits_name": guess_speaker(os.path.basename(sovits_path)),
                "epoch": holder["epoch"],
            },
        }
        self._queue.put_nowait(task_id)
        self._order.append(task_id)
        return task_id

    def avg_duration(self):
        return sum(self._durations) / len(self._durations) if self._durations else config.TASK_ETA_DEFAULT

    def position_of(self, task_id):
        """排队位置 = 正在运行的任务数 + 该任务在队列中的序号（0 = 马上开始）。"""
        try:
            idx = self._order.index(task_id)
        except ValueError:
            return 0  # 不在队列中（正在运行 / 已结束）
        return self._running + idx

    def task_view(self, task_id):
        """构造给前端的任务状态视图；任务不存在返回 None。"""
        task = self._tasks.get(task_id)
        if not task:
            return None
        now = time.time()
        status = task["status"]
        if status in ("done", "error"):
            elapsed = task["elapsed"] or 0
            estimated = 0
        else:
            elapsed = now - (task["started_at"] or task["created_at"])
            avg = self.avg_duration()
            if status == "running":
                estimated = max(0, round(avg - elapsed))
            else:
                pos = self.position_of(task_id)
                estimated = round(avg * ((pos + self.max_workers - 1) // self.max_workers))
        pos = self.position_of(task_id)
        voice = task.get("voice") or {}
        waiting_switch = (
            status == "queued" and voice.get("epoch") is not None
            and voice["epoch"] != model_switch_guard.current_epoch()
        )
        if waiting_switch:
            tip = ("排队第 %d 个 · 等待音色切换完成后用「%s」合成"
                   % (pos, voice.get("sovits_name") or "所选音色")) if pos else (
                "等待音色切换完成后用「%s」合成" % (voice.get("sovits_name") or "所选音色"))
        elif status == "queued":
            tip = f"当前排队第 {pos} 个，预计 {estimated} 秒" if pos else "即将开始合成"
        elif status == "running":
            tip = f"正在合成，预计还需 {estimated} 秒"
        elif status == "done":
            tip = "合成完成"
        else:
            tip = "合成失败"
        view = {
            "task_id": task["id"],
            "status": status,
            "status_url": None,
            "queue_position": pos,
            "queue_length": self._queue.qsize() + self._running,
            "estimated_seconds": estimated,
            "elapsed_seconds": round(elapsed, 1),
            "tip": tip,
            "play_url": None,
            "download_url": None,
            "error": None,
        }
        if voice:
            view["voice"] = {
                "sovits_name": voice.get("sovits_name"),
                "gpt_name": voice.get("gpt_name"),
                "epoch": voice.get("epoch"),
            }
        if status == "done":
            res = task["result"] or {}
            view["play_url"] = res.get("play_url")
            view["download_url"] = res.get("download_url")
        elif status == "error":
            view["error"] = task["error"]
        return view

    # ---------- 后台执行 ----------

    def start(self):
        """启动 MAX_CONCURRENT 个后台 worker（在 FastAPI startup 中调用）。"""
        for _ in range(self.max_workers):
            self._workers.append(asyncio.create_task(self._worker()))

    async def _worker(self):
        while True:
            task_id = await self._queue.get()
            task = self._tasks.get(task_id)
            try:
                if not task:
                    continue
                if task_id in self._order:
                    self._order.remove(task_id)
                self._running += 1
                task["status"] = "running"
                task["started_at"] = time.time()
                await self._run_task(task)
            except Exception:
                logger.exception("任务执行异常: %s", task_id)
                if task and task["status"] == "running":
                    task["status"] = "error"
                    task["error"] = "服务内部错误，请稍后重试"
                    task["finished_at"] = time.time()
                    task["elapsed"] = task["finished_at"] - task["started_at"]
            finally:
                self._running -= 1
                self._queue.task_done()

    async def _run_task(self, task):
        req = task["req"]
        try:
            sr, audio_data = await _synthesize(req, task["holder"])
            media_type = task["media_type"]
            audio_bytes = pack_audio(BytesIO(), audio_data, sr, media_type).getvalue()
            ext = "wav" if media_type == "wav" else media_type
            filename = f"{task['id']}.{ext}"
            with open(os.path.join(paths.TEMP_AUDIO_DIR, filename), "wb") as f:
                f.write(audio_bytes)
            token_suffix = f"?token={config.AUDIO_AUTH_TOKEN}" if config.AUDIO_AUTH_TOKEN else ""
            task["result"] = {
                "play_url": f"{task['base_url']}audio/{filename}{token_suffix}",
                "download_url": f"{task['base_url']}audio/{filename}{token_suffix}",
            }
            task["status"] = "done"
        except Exception:
            logger.exception("语音合成异常")
            send_alert(f"语音合成异常: text={req.get('text','')[:40]}")
            task["status"] = "error"
            task["error"] = "语音合成失败，请检查参数或稍后重试"
        finally:
            # 无论成败都注销音色代际登记，避免阻塞等待中的音色切换
            await model_switch_guard.release(task["holder"])
            task["finished_at"] = time.time()
            task["elapsed"] = task["finished_at"] - task["started_at"]
            if task["status"] == "done":
                self._durations.append(task["elapsed"])
            text = req.get("text", "")[:60]
            logger.info("TTS 任务 %s status=%s lang=%s text=\"%s\" took=%.1fs",
                        task["id"], task["status"], req.get("text_lang", "?"), text, task["elapsed"])

    def cleanup(self):
        """清理超时的任务状态与队列中的孤儿条目（由每小时定时任务调用）。"""
        now = time.time()
        for task_id in list(self._tasks.keys()):
            task = self._tasks[task_id]
            if (task["status"] in ("done", "error") and task["finished_at"]
                    and now - task["finished_at"] > config.TASK_TTL):
                del self._tasks[task_id]
        for task_id in list(self._order):
            if task_id not in self._tasks:
                self._order.remove(task_id)


task_queue = TaskQueue()
