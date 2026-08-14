#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPT-SoVITS 语音合成台 API —— 一键配置与依赖安装脚本
====================================================

把整个 GPT-SoVITS-API 文件夹放进 GPT-SoVITS 仓库根目录后，运行本脚本即可：

  1. 自动定位 GPT-SoVITS 仓库根目录，检查项目摆放位置；
  2. 检查 Python 版本、PyTorch / CUDA 是否可用；
  3. 检测并安装本项目额外依赖（requirements.txt，仅少量包）；
  4. 检查预训练模型（BERT / HuBERT / 默认权重）与 G2PW、ffmpeg 等运行时资源；
  5. 扫描 voices/ 音色库与仓库权重目录，报告可直接使用的音色；
  6. 自动把当前 Python 解释器写入 start.bat / start.sh 的占位符。

用法：
  python install_deps.py                交互式：询问后再安装 / 配置
  python install_deps.py --yes          全自动：安装缺失依赖并配置启动脚本
  python install_deps.py --check        只检查，不安装、不改任何文件
  python install_deps.py --no-install   只配置启动脚本，不安装依赖
  python install_deps.py --root D:\\GPT-SoVITS   手动指定 GPT-SoVITS 仓库根目录

说明：本脚本只负责“本项目”的少量额外依赖。GPT-SoVITS 本体环境
（torch、gradio 等）请先按官方教程安装（Windows: install.ps1，
Linux/macOS: install.sh），预训练模型也可用官方安装脚本一键下载。
"""

import argparse
import importlib
import os
import re
import shlex
import shutil
import subprocess
import sys

# ============================================================
# 常量
# ============================================================

API_DIR = os.path.dirname(os.path.abspath(__file__))
REQ_FILE = os.path.join(API_DIR, "requirements.txt")
START_BAT = os.path.join(API_DIR, "start.bat")
START_SH = os.path.join(API_DIR, "start.sh")

# 本项目 requirements.txt 中的依赖（用于 import 检测）
API_DEP_NAMES = ("fastapi", "uvicorn", "pydantic", "soundfile", "numpy")

# GPT-SoVITS 启动必需的两个预训练目录（缺失会导致服务启动失败）
REQUIRED_PRETRAIN_DIRS = ("chinese-roberta-wwm-ext-large", "chinese-hubert-base")

# 各模型版本的官方默认权重（有任意一组即可；用户音色模型可替代）
DEFAULT_T2S_WEIGHTS = (
    "s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
    os.path.join("gsv-v2final-pretrained", "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"),
    "s1v3.ckpt",
)
DEFAULT_VITS_WEIGHTS = (
    "s2G488k.pth",
    os.path.join("gsv-v2final-pretrained", "s2G2333k.pth"),
    os.path.join("v2Pro", "s2Gv2Pro.pth"),
    os.path.join("v2Pro", "s2Gv2ProPlus.pth"),
    "s2Gv3.pth",
    os.path.join("gsv-v4-pretrained", "s2Gv4.pth"),
)

# 音色扫描时跳过的目录（与 tts_api/voice_library.py 保持一致）
_SKIP_DIRS = {
    "pretrained_models", "output", "logs", ".git", "__pycache__",
    ".cache", "TEMP", "temp_audio", "runtime", "tools", "docs",
    "GPT_SoVITS", ".github", "Docker",
}

args = None


# ============================================================
# 基础输出工具
# ============================================================

def _ensure_utf8_console():
    """让中文输出在 Windows 控制台下也能正确显示。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def info(msg):
    print("[信息] " + msg)


def ok(msg):
    print("[OK]   " + msg)


def warn(msg):
    print("[警告] " + msg)


def err(msg):
    print("[错误] " + msg)


def ask_yes_no(question, default=True):
    """交互式确认；--yes 时直接返回默认值，EOF 时也返回默认值。"""
    if args.yes:
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input("%s %s " % (question, suffix)).strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        if answer in ("y", "yes", "是", "确认", "1"):
            return True
        if answer in ("n", "no", "否", "0"):
            return False
        print("  请输入 y / n")


# ============================================================
# 1. 定位 GPT-SoVITS 仓库根目录
# ============================================================

def _has_gptsovits(candidate):
    return bool(candidate) and os.path.isdir(os.path.join(candidate, "GPT_SoVITS"))


def find_gptsovits_root(explicit):
    if explicit:
        explicit = os.path.abspath(explicit)
        if not os.path.isdir(explicit):
            err("指定的 --root 目录不存在: %s" % explicit)
            sys.exit(1)
        if not _has_gptsovits(explicit):
            err("指定的 --root 下未找到 GPT_SoVITS 目录: %s" % explicit)
            sys.exit(1)
        return explicit

    # 1) 从当前工作目录逐级向上找（允许在仓库内任意子目录启动）
    cur = os.path.abspath(os.getcwd())
    for _ in range(5):
        if _has_gptsovits(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # 2) 标准布局兜底: <仓库根>/GPT-SoVITS-API/
    standard = os.path.dirname(API_DIR)
    if _has_gptsovits(standard):
        return standard
    return None


# ============================================================
# 2. Python / PyTorch 检查
# ============================================================

def check_python(issues):
    version = sys.version.split()[0]
    info("Python: %s（%s）" % (version, sys.executable))
    if sys.version_info < (3, 9):
        warn("Python 版本过低，GPT-SoVITS 官方推荐 3.9 ~ 3.11，请换用其环境再试。")
        issues.append(("err", "Python 版本过低（%s），请使用 GPT-SoVITS 官方环境（3.9 ~ 3.11）" % version))
    elif sys.version_info >= (3, 12):
        warn("Python 版本较高（>=3.12），GPT-SoVITS 官方推荐 3.9 ~ 3.11，部分依赖可能不兼容。")
        issues.append(("warn", "Python 版本为 %s，官方推荐 3.9 ~ 3.11" % version))


def check_torch(issues):
    try:
        import torch
    except ImportError:
        err("未检测到 PyTorch。GPT-SoVITS 本体环境尚未安装，请先按官方教程安装")
        err("（Windows: install.ps1；Linux/macOS: install.sh），本脚本不会安装 GPT-SoVITS 本体依赖。")
        issues.append(("err", "未安装 PyTorch / GPT-SoVITS 本体环境（请先运行官方安装脚本）"))
        return False
    cuda = torch.cuda.is_available()
    info("PyTorch: %s，CUDA 可用: %s" % (torch.__version__, "是" if cuda else "否"))
    if cuda:
        try:
            info("GPU: %s" % torch.cuda.get_device_name(0))
        except Exception:
            pass
    else:
        warn("未检测到可用 CUDA GPU，启动时请使用 --device cpu（半精度会自动关闭）。")
        issues.append(("warn", "未检测到 CUDA GPU，需以 CPU 模式运行"))
    return True


# ============================================================
# 3. 本项目额外依赖检查 / 安装
# ============================================================

def check_api_deps(issues):
    missing = []
    for name in API_DEP_NAMES:
        try:
            mod = importlib.import_module(name)
            ver = getattr(mod, "__version__", "?")
            ok("%s %s" % (name, ver))
        except ImportError:
            missing.append(name)
    if missing:
        warn("缺少本项目依赖: %s" % ", ".join(missing))
        issues.append(("warn", "缺少本项目依赖: %s（运行 install_deps.py 自动安装）" % ", ".join(missing)))
    return missing


def install_api_deps(issues):
    if not os.path.isfile(REQ_FILE):
        err("未找到 %s，无法安装依赖。" % REQ_FILE)
        return False
    info("正在执行: %s -m pip install -r requirements.txt ..." % sys.executable)
    print("-" * 60)
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", REQ_FILE])
    print("-" * 60)
    if rc != 0:
        err("pip 安装失败（退出码 %d），请检查网络或手动执行上述命令。" % rc)
        return False
    # 安装后复查
    still_missing = []
    for name in API_DEP_NAMES:
        try:
            importlib.import_module(name)
        except ImportError:
            still_missing.append(name)
    if still_missing:
        err("以下依赖仍无法导入: %s" % ", ".join(still_missing))
        if "soundfile" in still_missing:
            err("soundfile 通常需要系统库 libsndfile：")
            err("  Ubuntu/Debian: sudo apt install libsndfile1")
            err("  conda 环境: conda install -c conda-forge libsndfile")
        return False
    ok("本项目额外依赖已全部就绪。")
    return True


# ============================================================
# 4. 预训练模型 / G2PW / ffmpeg 检查
# ============================================================

def _pretrain_dir(root):
    return os.path.join(root, "GPT_SoVITS", "pretrained_models")


def check_pretrained(root, issues):
    pretrain = _pretrain_dir(root)
    info("检查预训练模型: %s" % pretrain)
    if not os.path.isdir(pretrain):
        err("未找到预训练模型目录。请先运行 GPT-SoVITS 官方安装脚本下载，")
        err("或手动把 chinese-roberta-wwm-ext-large、chinese-hubert-base 放到该目录。")
        issues.append(("err", "预训练模型目录缺失: %s" % pretrain))
        return

    for name in REQUIRED_PRETRAIN_DIRS:
        if os.path.isdir(os.path.join(pretrain, name)):
            ok("预训练模型目录: %s" % name)
        else:
            err("缺少必需预训练目录: %s（缺失会导致服务启动失败）" % name)
            issues.append(("err", "缺少必需预训练目录: %s" % name))

    # v3/v4 人声编码器目录（官方安装脚本的下载完成标记）
    if os.path.isdir(os.path.join(pretrain, "sv")):
        ok("预训练模型目录: sv（v3/v4 人声编码器）")
    else:
        warn("未找到 sv 目录（v3/v4 模型需要，v1/v2 可忽略）。")
        issues.append(("warn", "未找到 pretrained_models/sv（v3/v4 需要）"))

    found_t2s = sum(1 for rel in DEFAULT_T2S_WEIGHTS if os.path.isfile(os.path.join(pretrain, rel)))
    found_vits = sum(1 for rel in DEFAULT_VITS_WEIGHTS if os.path.isfile(os.path.join(pretrain, rel)))
    if found_t2s == 0:
        warn("未找到任何官方默认 GPT 权重（s1bert/s1v3.ckpt）。用户自训音色模型可替代。")
        issues.append(("warn", "未找到官方默认 GPT 权重（有自训音色则无碍）"))
    if found_vits == 0:
        warn("未找到任何官方默认 SoVITS 权重（s2G*.pth）。用户自训音色模型可替代。")
        issues.append(("warn", "未找到官方默认 SoVITS 权重（有自训音色则无碍）"))

    g2pw = os.path.join(root, "GPT_SoVITS", "text", "G2PWModel")
    if os.path.isdir(g2pw):
        ok("G2PWModel（中文注音）已就绪")
    else:
        warn("未找到 G2PWModel（中文 G2P 需要，官方安装脚本会下载）。")
        issues.append(("warn", "未找到 GPT_SoVITS/text/G2PWModel"))

    tts_yaml = os.path.join(root, "GPT_SoVITS", "configs", "tts_infer.yaml")
    if os.path.isfile(tts_yaml):
        ok("GPT_SoVITS 配置文件: tts_infer.yaml")
    else:
        err("未找到 GPT_SoVITS/configs/tts_infer.yaml，仓库文件可能不完整。")
        issues.append(("err", "缺少 GPT_SoVITS/configs/tts_infer.yaml"))


def check_ffmpeg(issues):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        ok("ffmpeg: %s" % ffmpeg)
    else:
        warn("未检测到 ffmpeg（仅 aac 格式输出需要；wav/ogg/raw 不受影响）。")
        issues.append(("warn", "未检测到 ffmpeg（aac 输出需要）"))


# ============================================================
# 5. 音色库扫描
# ============================================================

def _library_dirs(root):
    dirs = []
    voice_dir = os.path.join(API_DIR, "voices")
    if os.path.isdir(voice_dir):
        dirs.append(voice_dir)
    for name in sorted(os.listdir(root)):
        if name.startswith(("GPT_weights", "SoVITS_weights")):
            dirs.append(os.path.join(root, name))
    return dirs


def _guess_speaker(name):
    """从文件名猜测音色名（与 tts_api/voice_library.py 一致）：MyVoice-e15.ckpt -> MyVoice"""
    stem = os.path.splitext(name)[0]
    stem = re.sub(r"[-_](e\d+)([-_]s\d+)?$", "", stem)
    stem = re.sub(r"[-_](s\d+)$", "", stem)
    stem = re.sub(r"(\s*语音|\s*ボイス|voice)$", "", stem, flags=re.I)
    stem = stem.strip(" -_")
    return stem or "未命名"


def scan_voices(root, issues):
    info("扫描音色库（voices/ 与 GPT_weights*/SoVITS_weights*）...")
    dirs = _library_dirs(root)
    # 与 voice_library.py 一致：按音色名跨目录聚合 GPT / SoVITS / 参考音频
    by_speaker = {}
    for base in dirs:
        base_abs = os.path.abspath(base)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            if dirpath[len(base_abs):].count(os.sep) > 3:
                dirnames[:] = []
            label = os.path.relpath(dirpath, API_DIR)
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1].lower()
                speaker = _guess_speaker(fn)
                entry = by_speaker.setdefault(
                    speaker, {"gpt": [], "sovits": [], "refs": [], "dirs": []}
                )
                if ext == ".ckpt":
                    entry["gpt"].append(fn)
                elif ext in (".pth", ".pt"):
                    entry["sovits"].append(fn)
                elif ext == ".wav":
                    entry["refs"].append(fn)
                else:
                    continue
                if label not in entry["dirs"]:
                    entry["dirs"].append(label)

    if not by_speaker:
        warn("未扫描到任何音色模型。请把 .ckpt + .pth 放进 voices/<音色名>/ 后再启动。")
        issues.append(("warn", "未扫描到音色模型（voices/<音色名>/ 内放 .ckpt + .pth）"))
        return

    usable = 0
    for speaker in sorted(by_speaker):
        entry = by_speaker[speaker]
        gpt, sovits, refs, dirs = entry["gpt"], entry["sovits"], entry["refs"], entry["dirs"]
        loc = " / ".join(dirs)
        if gpt and sovits:
            usable += 1
            ok("音色可用: %s（GPT %d / SoVITS %d / 参考音频 %d，位于 %s）"
               % (speaker, len(gpt), len(sovits), len(refs), loc))
        elif gpt:
            warn("音色缺 SoVITS 模型: %s（仅 GPT %d 个，位于 %s）" % (speaker, len(gpt), loc))
            issues.append(("warn", "音色缺 SoVITS 模型: %s" % speaker))
        elif sovits:
            warn("音色缺 GPT 模型: %s（仅 SoVITS %d 个，位于 %s）" % (speaker, len(sovits), loc))
            issues.append(("warn", "音色缺 GPT 模型: %s" % speaker))
        else:
            info("仅参考音频: %s（%d 条，位于 %s）" % (speaker, len(refs), loc))
    if usable:
        ok("共检测到 %d 个可直接使用的音色。" % usable)
    else:
        err("没有找到同时含 .ckpt 与 .pth 的完整音色，请检查 voices/ 目录。")
        issues.append(("err", "没有完整音色（需同一目录内 .ckpt + .pth）"))


# ============================================================
# 6. 自动配置启动脚本中的 Python 解释器
# ============================================================

def _read_text(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def _write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def configure_launchers(issues):
    if os.environ.get("TTS_API_LAUNCHER_RUN") == "1":
        info("本次由 start.bat / start.sh 调用，跳过启动脚本改写（避免运行时修改自身）。")
        return

    python_path = sys.executable
    if os.name == "nt" and os.path.isfile(START_BAT):
        _configure_start_bat(python_path, issues)
    elif os.name != "nt" and os.path.isfile(START_SH):
        _configure_start_sh(python_path, issues)
    else:
        info("未找到当前平台的启动脚本（%s），跳过自动配置。" % ("start.bat" if os.name == "nt" else "start.sh"))


def _configure_start_bat(python_path, issues):
    if '"' in python_path:
        warn("Python 路径含双引号，无法安全写入 start.bat，请手动配置。")
        return
    text = _read_text(START_BAT)
    m = re.search(r'(?m)^set "PYTHON_EXE=([^"]*)"', text)
    if not m:
        warn("start.bat 中未找到 PYTHON_EXE 行，跳过自动配置。")
        return
    current = m.group(1).strip()
    if current not in ("python", "python3"):
        if args.yes:
            info("start.bat 已配置自定义解释器 %s，保留不动。" % current)
            return
        if ask_yes_no(
            "start.bat 已配置解释器 %s，是否替换为当前解释器 %s？" % (current, python_path),
            default=False,
        ):
            pass
        else:
            info("保留 start.bat 中已有的自定义解释器路径。")
            return
    # 占位符（python / python3）→ 交互式询问后替换；--yes 直接替换
    if not (args.yes or ask_yes_no(
        "检测到 start.bat 使用占位符 %s，是否自动改为当前解释器路径？\n  %s" % (current, python_path),
        default=True,
    )):
        info("保留 start.bat 中的占位符，未改动。")
        return
    # 批处理中 % 需写成 %% 才能原样存储
    escaped = python_path.replace("%", "%%")
    new_line = 'set "PYTHON_EXE=%s"' % escaped
    # 只替换到行尾引号为止，保留原有换行符（CRLF 不被破坏）
    text = re.sub(r'(?m)^set "PYTHON_EXE=[^"]*"', lambda m: new_line, text, count=1)
    _write_text(START_BAT, text)
    ok("已自动配置 start.bat → PYTHON_EXE=%s" % python_path)


def _configure_start_sh(python_path, issues):
    text = _read_text(START_SH)
    m = re.search(r'(?m)^PYTHON_EXE="\$\{PYTHON_EXE:-([^}]*)\}"', text)
    if not m:
        warn("start.sh 中未找到 PYTHON_EXE 行，跳过自动配置。")
        return
    current = m.group(1).strip().strip("'\"")
    if current not in ("python", "python3"):
        if args.yes:
            info("start.sh 已配置自定义解释器 %s，保留不动。" % current)
            return
        if ask_yes_no(
            "start.sh 已配置解释器 %s，是否替换为当前解释器 %s？" % (current, python_path),
            default=False,
        ):
            pass
        else:
            info("保留 start.sh 中已有的自定义解释器路径。")
            return
    # 占位符（python / python3）→ 交互式询问后替换；--yes 直接替换
    if not (args.yes or ask_yes_no(
        "检测到 start.sh 使用占位符 %s，是否自动改为当前解释器路径？\n  %s" % (current, python_path),
        default=True,
    )):
        info("保留 start.sh 中的占位符，未改动。")
        return
    quoted = shlex.quote(python_path)
    block = "PYTHON_EXE_DEFAULT=%s\nPYTHON_EXE=\"${PYTHON_EXE:-$PYTHON_EXE_DEFAULT}\"" % quoted
    # 保留原有换行符，只替换该行内容
    text = re.sub(r'(?m)^PYTHON_EXE="\$\{PYTHON_EXE:-[^}]*\}"', lambda m: block, text, count=1)
    _write_text(START_SH, text)
    ok("已自动配置 start.sh → PYTHON_EXE_DEFAULT=%s" % quoted)


# ============================================================
# 汇总
# ============================================================

def print_summary(issues):
    print()
    print("=" * 60)
    print("检查结果汇总")
    print("=" * 60)
    err_count = sum(1 for level, _ in issues if level == "err")
    warn_count = sum(1 for level, _ in issues if level == "warn")
    for level, msg in issues:
        if level == "err":
            err(msg)
        else:
            warn(msg)
    print("-" * 60)
    if err_count:
        err("%d 个问题需要处理（%d 个警告）。" % (err_count, warn_count))
    elif warn_count:
        warn("无致命问题，但有 %d 个警告。" % warn_count)
    else:
        ok("全部就绪，可以直接启动服务！")
    print()
    print("下一步：")
    print("  Windows : 双击 start.bat（首次运行会自动安装缺失依赖）")
    print("  Linux   : bash start.sh")
    print("  手动    : python api.py -a 0.0.0.0 -p 9880")
    print("  前台    : http://127.0.0.1:9880/")
    print("  API文档 : http://127.0.0.1:9880/docs")


# ============================================================
# 主流程
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="GPT-SoVITS 语音合成台 API：一键检查环境、安装依赖、配置启动脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check", action="store_true",
                        help="只检查环境并输出报告，不安装依赖、不修改任何文件")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="全自动模式：不再询问，直接安装缺失依赖并配置启动脚本占位符")
    parser.add_argument("--no-install", action="store_true",
                        help="跳过 pip 依赖安装（仅检查与配置启动脚本）")
    parser.add_argument("--no-launchers", action="store_true",
                        help="跳过 start.bat / start.sh 自动配置")
    parser.add_argument("--root", default="",
                        help="手动指定 GPT-SoVITS 仓库根目录（含 GPT_SoVITS 目录的那一层）")
    return parser.parse_args()


def main():
    global args
    _ensure_utf8_console()
    args = parse_args()

    print("=" * 60)
    print("GPT-SoVITS 语音合成台 API —— 一键配置与依赖安装")
    print("=" * 60)
    print()

    root = find_gptsovits_root(args.root)
    if not root:
        err("未找到 GPT-SoVITS 仓库根目录（含 GPT_SoVITS 包的目录）。")
        err("请把本文件夹放进 GPT-SoVITS 仓库根目录后重试，或用 --root 指定。")
        sys.exit(1)
    ok("GPT-SoVITS 仓库根目录: %s" % root)
    ok("本项目目录: %s" % API_DIR)
    print()

    issues = []

    check_python(issues)
    torch_ok = check_torch(issues)
    if not torch_ok:
        if not args.check:
            if args.yes:
                err("请先安装 GPT-SoVITS 本体环境再运行本脚本。")
                sys.exit(1)
            if not ask_yes_no("GPT-SoVITS 本体环境未安装，是否仍要继续配置本项目？"
                              "（装好本体之前服务无法启动）", default=False):
                sys.exit(1)
    print()

    missing = check_api_deps(issues)
    if missing and not args.check and not args.no_install:
        print()
        if args.yes or ask_yes_no("是否现在用 pip 安装这些缺失依赖？（使用当前解释器 %s）" % sys.executable,
                                  default=True):
            if not install_api_deps(issues):
                sys.exit(1)
    print()

    check_pretrained(root, issues)
    check_ffmpeg(issues)
    print()

    scan_voices(root, issues)
    print()

    if not args.check and not args.no_launchers:
        configure_launchers(issues)
        print()

    print_summary(issues)


if __name__ == "__main__":
    main()
