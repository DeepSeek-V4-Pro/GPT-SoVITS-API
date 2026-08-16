"""
工作目录与依赖路径初始化
=========================
本服务把 GPT-SoVITS 仓库根目录（包含 GPT_SoVITS 包的目录）作为「仓库根目录」，
并在导入任何 GPT_SoVITS 内部模块之前完成 sys.path 注入与 chdir。
任何依赖仓库根目录 / GPT_SoVITS / 前端文件的模块都必须在文件开头导入本模块。

标准布局（把本项目放进 GPT-SoVITS 仓库根目录）:

    <GPT-SoVITS 仓库根>/
    ├── GPT_SoVITS/            # GPT-SoVITS 本体（本项目不包含）
    ├── GPT_weights*/ ...      # 官方权重目录（可选，会被一并扫描）
    └── GPT-SoVITS-API/        # 本项目
        ├── api.py
        ├── tts_api/...        # 本包
        └── voices/            # 音色库（本项目内置子目录）
"""

import os
import sys

# 本包所在目录（tts_api/）
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
# 本项目目录（GPT-SoVITS-API/）
API_ROOT_DIR = os.path.dirname(PACKAGE_DIR)


def _find_gptsovits_root():
    """自动定位 GPT-SoVITS 仓库根目录（含 GPT_SoVITS 包的那一层）。"""

    def _has_gptsovits(candidate):
        return bool(candidate) and os.path.isdir(os.path.join(candidate, "GPT_SoVITS"))

    # 1) 从启动时的工作目录逐级向上找（允许在仓库内任意子目录启动）
    cur = os.path.abspath(os.getcwd())
    for _ in range(4):
        if _has_gptsovits(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # 2) 标准布局兜底: <仓库根>/GPT-SoVITS-API/tts_api/
    standard = os.path.dirname(API_ROOT_DIR)
    if _has_gptsovits(standard):
        return standard
    # 3) 都不满足时沿用旧行为（以工作目录为根）
    return os.path.abspath(os.getcwd())


# 仓库根目录 = GPT-SoVITS 仓库根目录
NOW_DIR = _find_gptsovits_root()
if not os.path.isdir(os.path.join(NOW_DIR, "GPT_SoVITS")):
    sys.stderr.write("[警告] 未在 %s 及其上级目录找到 GPT_SoVITS 包，"
                     "请确认本项目已放入 GPT-SoVITS 仓库根目录。\n" % NOW_DIR)

# 保证可导入仓库根目录下的模块与 GPT_SoVITS 包
sys.path.append(NOW_DIR)
sys.path.append(os.path.join(NOW_DIR, "GPT_SoVITS"))

# 切到仓库根目录运行: GPT-SoVITS 内部大量使用相对路径（如 GPT_SoVITS/configs/），
# 只有 cwd = 仓库根时行为才与官方 WebUI 一致。
os.chdir(NOW_DIR)

# ---- 数据目录（内置在本项目目录下，可用环境变量覆盖）----
LOG_DIR = os.environ.get("TTS_API_LOG_DIR") or os.path.join(API_ROOT_DIR, "logs")
FEEDBACK_DIR = os.environ.get("TTS_API_FEEDBACK_DIR") or os.path.join(API_ROOT_DIR, "feedback")

# ---- 音色库与临时音频 ----
VOICE_DIR = os.path.join(API_ROOT_DIR, "voices")            # 音色库（每个子目录一个音色）
TEMP_AUDIO_DIR = os.path.join(API_ROOT_DIR, "temp_audio")   # 合成音频临时目录

# ---- 前端文件（随包分发: tts_api/frontend/）----
FRONTEND_DIR = os.path.join(PACKAGE_DIR, "frontend")
STUDIO_ASSETS_DIR = os.path.join(FRONTEND_DIR, "assets")
STUDIO_HTML = os.path.join(FRONTEND_DIR, "index.html")
CHAT_HTML = os.path.join(FRONTEND_DIR, "chat.html")

# 公告内容覆盖文件（可选）：创建本文件后，合成台公告弹窗显示其内容
# （首行为标题，空一行后为正文，支持 [文字](链接) 语法）；不创建则用代码内默认公告。
NOTICE_FILE = os.path.join(FRONTEND_DIR, "notice.md")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(FEEDBACK_DIR, exist_ok=True)
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
