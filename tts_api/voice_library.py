"""
音色库扫描与模型路径校验
========================
- scan_voice_library: 扫描全部模型目录，按音色分组返回 GPT/SoVITS 模型与参考音频
- is_allowed_model_path: 模型热切换路径白名单校验
- guess_speaker / _epoch_key 等内部工具

「当前加载模型」信息由 system 路由从 engine.MODEL_CFG 组装（见 routers/system.py），
本模块只负责扫描，避免与 engine 产生循环依赖。
"""

import os
import re

from . import config, paths

# 主音色库目录: 每个子目录 = 一个音色，里面放该音色的全部模型与参考音频。
# 目录约定:
#   voices/
#     我的音色/
#       我的音色-e15.ckpt        # GPT 模型（可放多个 epoch，默认取最高的）
#       我的音色_e8_s184.pth     # SoVITS 模型（可放多个，默认取最高的）
#       参考音频.wav             # 参考音频（可多条）
#       ...
# 新音色只需在 voices/ 下建子目录并放入文件，前台点「刷新」即可自选。

# 每个目录最多展示的参考音频数量（防止数据集目录成千上万条塞爆下拉框）
REF_MAX_PER_DIR = 60

GPT_EXTS = {".ckpt"}
SOVITS_EXTS = {".pth", ".pt"}
_SKIP_DIRS = {"pretrained_models", "output", "logs", ".git", "__pycache__",
              ".cache", "TEMP", "temp_audio", "runtime", "tools", "docs",
              "GPT_SoVITS", ".github", "Docker"}


def _library_dirs():
    """模型搜索目录 = voices 主目录 + 仓库权重目录 + EXTRA_MODEL_DIRS（去重，仅保留存在的）"""
    dirs = []
    if os.path.isdir(paths.VOICE_DIR):
        dirs.append(paths.VOICE_DIR)
    for name in sorted(os.listdir(paths.NOW_DIR)):
        if name.startswith(("GPT_weights", "SoVITS_weights")):
            dirs.append(os.path.join(paths.NOW_DIR, name))
    for d in config.EXTRA_MODEL_DIRS:
        if os.path.isdir(d):
            dirs.append(d)
    seen, out = set(), []
    for d in dirs:
        real = os.path.realpath(d)
        if real not in seen:
            seen.add(real)
            out.append(d)
    return out


def guess_speaker(name):
    """从文件名/目录名猜测音色名: 我的音色-e15.ckpt -> 我的音色"""
    name = os.path.splitext(name)[0]
    name = re.sub(r"[-_](e\d+)([-_]s\d+)?$", "", name)
    name = re.sub(r"[-_](s\d+)$", "", name)
    name = re.sub(r"(\s*语音|\s*ボイス|voice)$", "", name, flags=re.I)
    name = name.strip(" -_")
    return name or "未命名"


def _ref_lang_hint(name):
    if re.search(r"[\u3040-\u30ff]", name):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", name):
        return "zh"
    return "auto"


def _epoch_key(item):
    """按训练 epoch 从高到低排序: e15 > e10 > e5; e8_s184 > e4_s92（默认加载最高的）"""
    stem = os.path.splitext(item["name"])[0]
    e = [int(x) for x in re.findall(r"[eE](\d+)", stem)]
    s = [int(x) for x in re.findall(r"[sS](\d+)", stem)]
    return (-(max(e) if e else 0), -(max(s) if s else 0), item["name"])


def scan_voice_library():
    """扫描全部模型目录，返回按音色分组的模型与参考音频列表"""
    gpt_models, sovits_models, ref_audios = [], [], []
    for root in _library_dirs():
        if not os.path.isdir(root):
            continue
        root_abs = os.path.abspath(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            if dirpath[len(root_abs):].count(os.sep) > 3:
                dirnames[:] = []
            dir_label = os.path.basename(dirpath) or os.path.basename(root_abs)
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1].lower()
                full = os.path.join(dirpath, fn)
                try:
                    size_mb = round(os.path.getsize(full) / 1048576, 1)
                    mtime = int(os.path.getmtime(full))
                except OSError:
                    continue
                if ext in GPT_EXTS:
                    gpt_models.append({
                        "name": fn, "path": full, "dir": dir_label,
                        "speaker": guess_speaker(fn), "size_mb": size_mb, "mtime": mtime,
                    })
                elif ext in SOVITS_EXTS:
                    sovits_models.append({
                        "name": fn, "path": full, "dir": dir_label,
                        "speaker": guess_speaker(fn), "size_mb": size_mb, "mtime": mtime,
                    })
                elif ext == ".wav":
                    stem = os.path.splitext(fn)[0]
                    ref_audios.append({
                        "name": fn, "path": full, "dir": dir_label,
                        "speaker": guess_speaker(dir_label),
                        "lang_hint": _ref_lang_hint(fn),
                        "sentence_like": bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", stem)),
                        "size_mb": size_mb, "mtime": mtime,
                    })
    # 额外扫描 REF_SEARCH_DIRS 中不在模型目录里的参考音频（限两层深度）
    for root in config.REF_SEARCH_DIRS:
        if not os.path.isdir(root):
            continue
        root_abs = os.path.abspath(root)
        if any(root_abs == os.path.realpath(d) for d in _library_dirs()):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            if dirpath[len(root_abs):].count(os.sep) >= 1:
                dirnames[:] = []
            for fn in sorted(filenames):
                if not fn.lower().endswith(".wav"):
                    continue
                full = os.path.join(dirpath, fn)
                if not os.path.isfile(full):
                    continue
                try:
                    size_mb = round(os.path.getsize(full) / 1048576, 1)
                    mtime = int(os.path.getmtime(full))
                except OSError:
                    continue
                stem = os.path.splitext(fn)[0]
                ref_audios.append({
                    "name": fn, "path": full, "dir": os.path.basename(dirpath),
                    "speaker": guess_speaker(os.path.basename(dirpath)),
                    "lang_hint": _ref_lang_hint(fn),
                    "sentence_like": bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", stem)),
                    "size_mb": size_mb, "mtime": mtime,
                })
    # 参考音频按目录限量，保留最新的
    per_dir = {}
    for r in ref_audios:
        per_dir.setdefault(r["dir"], []).append(r)
    ref_audios = []
    for _d, items in per_dir.items():
        items.sort(key=lambda x: x.get("mtime", 0), reverse=True)
        ref_audios.extend(items[:REF_MAX_PER_DIR])

    # 同一模型可能同时存在于仓库目录与 EXTRA 目录，按 (音色名, 文件名) 去重，保留先扫描到的
    def _dedupe(items):
        seen, out = set(), []
        for it in items:
            key = (it["speaker"], it["name"])
            if key not in seen:
                seen.add(key)
                out.append(it)
        return out

    gpt_models = _dedupe(gpt_models)
    sovits_models = _dedupe(sovits_models)

    def by_speaker(items):
        out = {}
        for it in items:
            out.setdefault(it["speaker"], []).append(it)
        return out

    gpt_by = by_speaker(gpt_models)
    sovits_by = by_speaker(sovits_models)
    refs_by = by_speaker(ref_audios)
    voices = []
    for sp in sorted(set(gpt_by) | set(sovits_by) | set(refs_by)):
        voices.append({
            "speaker": sp,
            "gpt": sorted(gpt_by.get(sp, []), key=_epoch_key),
            "sovits": sorted(sovits_by.get(sp, []), key=_epoch_key),
            "refs": sorted(refs_by.get(sp, []), key=lambda x: x["name"]),
        })
    return {
        "voices": voices,
        "gpt_models": gpt_models,
        "sovits_models": sovits_models,
        "ref_audios": ref_audios,
    }


def is_allowed_model_path(path: str) -> bool:
    """模型热切换仅允许加载扫描目录内的权重文件"""
    if not path or ".." in path:
        return False
    abs_path = os.path.abspath(path)
    for root in _library_dirs():
        root_abs = os.path.abspath(root)
        if abs_path == root_abs or abs_path.startswith(root_abs + os.sep):
            return True
    return False
