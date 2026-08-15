"""
运行时配置
==========
集中管理全部可调参数：预设参考音频、模型扫描目录、安全防护阈值、
音色切换协调、AI 对话参数，以及命令行参数解析。

可变设置（ALLOWED_IPS / MAX_TEXT_LENGTH 等）由 apply_args() 在导入时
根据命令行参数改写，其余模块一律通过「config.属性」访问以读取最新值。
"""

import argparse
import os

from . import paths

# ============================================================
# 预设参考音频（占位符：请替换为你自己的音色路径，或在前台选择
# /models 返回的参考音频。文件名即参考文本时合成效果最好。）
# ============================================================
DEFAULT_REF_AUDIO_PATH = os.path.join(
    paths.VOICE_DIR, "example", "example_ref.wav"
)
DEFAULT_PROMPT_TEXT = ""        # 参考音频对应文本（留空 = 零样本合成）
DEFAULT_PROMPT_LANG = "zh"

# ============================================================
# 安全防护
# ============================================================

# 除 voices/ 与仓库自带的 GPT_weights*/SoVITS_weights* 目录外，额外扫描的模型目录。
# 把训练好的 .ckpt / .pth 连同参考音频 .wav 放进任意一个目录也可被发现。
EXTRA_MODEL_DIRS = [
    # r"D:\path\to\extra_models",  # 示例：取消注释并改成你的目录
]

# 参考音频搜索目录（前台「参考音频」下拉框的来源）
REF_SEARCH_DIRS = [
    # r"D:\path\to\ref_audios",    # 示例：数据集原声目录
]

# 频率限制: 每分钟每个 IP 最多请求数（仅作用于 /tts /play /chat /set_* /feedback 等重资源端点）
RATE_LIMIT_MAX = 40
RATE_LIMIT_WINDOW = 60  # 秒

# 全局生成总量限制: 每分钟所有 IP 合计最多请求数
GLOBAL_RATE_LIMIT_MAX = 100

# 请求体大小上限 (10MB)
MAX_BODY_SIZE = 10 * 1024 * 1024

# 最大并发请求数（防止过载）
MAX_CONCURRENT = 2

# 排队任务上限（超出返回 503，防止任务无限堆积）
MAX_QUEUE_SIZE = 50

# 暂无历史耗时数据时，单个合成任务的 ETA 估算（秒）
TASK_ETA_DEFAULT = 30

# 任务状态保留时长（秒，与音频临时文件一致）
TASK_TTL = 3600

# 禁止合成的敏感内容关键词
BLOCKED_KEYWORDS = [
    "法轮功", "六四", "天安门事件", "藏独", "疆独", "台独",
    "色情", "赌博", "毒品", "枪支", "炸药", "炸弹",
    "出售银行卡", "代办信用卡", "诈骗", "传销",
]

# IP 白名单: 非空时仅允许列表内 IP 访问 TTS/play 端点的非本地请求
ALLOWED_IPS = set()
# IP 黑名单: 始终拒绝
BLOCKED_IPS = set()
# CORS 允许域名: 空列表 = 仅同源；"*" = 任意来源
CORS_ORIGINS = []
# 输入文本最大长度（超出截断）
MAX_TEXT_LENGTH = 500
# 音频访问令牌: 非空时 /audio 需要 ?token= 校验
AUDIO_AUTH_TOKEN = ""
# 告警 Webhook URL
WEBHOOK_URL = ""
# 是否要求 Referer 头
REQUIRE_REFERER = False

# ============================================================
# 音色切换协调（多人共用时防止“打架”）
# ============================================================

# 切换令牌: 非空时 /set_voice、/set_gpt_weights、/set_sovits_weights 需要 ?token= 校验，
# 只有持有令牌的人才能切音色（多人使用时可选开启）
SWITCH_AUTH_TOKEN = ""
# 两次成功切换的最小间隔（秒），0 = 不限。防止 A→B→A 反复切换造成模型反复加载
SWITCH_MIN_INTERVAL = 0

# ============================================================
# AI 语音对话（测试版）: 用户自带 OpenAI 兼容 API Key
# ============================================================

# 对话接口单独的频率限制: 每分钟每个 IP 最多对话轮数
CHAT_RATE_LIMIT_MAX = 10
CHAT_RATE_LIMIT_WINDOW = 60  # 秒

# 携带的历史对话轮数上限（每条历史消息截断 1000 字符）
CHAT_HISTORY_TURNS = 20

# 单次请求注入的历史记忆片段数上限（前端从浏览器保存的全部聊天记录中检索）
CHAT_MEMORY_HINTS_MAX = 8
CHAT_MEMORY_HINT_CHARS = 200

# 模型回复默认最大 token 数
CHAT_MAX_TOKENS = 1024

# LLM 调用超时（秒）
CHAT_LLM_TIMEOUT = 60

# 获取可用模型列表（/chat/models）的超时（秒）
CHAT_MODELS_TIMEOUT = 20

# 默认人设（系统提示词）: 音色目录无 persona.txt 时使用。
# 推荐给每个音色目录放 persona.txt，即可按音色自动切换人设。
DEFAULT_CHAT_SYSTEM_PROMPT = (
    "你的名字是 AI 助手。\n"
    "你是一位元气满满的虚拟主播，正在和朋友闲聊。\n\n"
    "现在请你读读之前的聊天记录，把握当前的话题，然后给出日常且口语化的回复，\n"
    "用户用什么语言提问，你就用什么语言回答。语气自然、简短，像真人聊天一样，不要长篇大论。\n\n"
    "在该聊天中的注意事项：\n"
    "通用注意事项：\n"
    "一次围绕一个话题回复，不要刻意找话题、不要过度展开，不要在聊天文字中加入emoji符号。\n\n"
    "请注意不要输出多余内容(包括不必要的前后缀，冒号，括号，表情包，@等)，只输出发言内容就好。"
    "你的回复会直接交给语音合成朗读：只输出纯文本正文，不要Markdown、列表、代码或表情符号，"
    "不要用括号写动作或心理活动，控制在300字以内。"
)

# ============================================================
# 命令行参数解析
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GPT-SoVITS 语音合成 API（语音合成台）")
    parser.add_argument("-a", "--bind_addr", type=str, default="0.0.0.0", help="绑定地址 (默认 0.0.0.0)")
    parser.add_argument("-p", "--port", type=int, default=9880, help="绑定端口 (默认 9880)")
    parser.add_argument("--version", type=str, default=os.environ.get("version") or "v2",
                        help="模型版本: v1/v2/v3/v4/v2Pro/v2ProPlus，须与模型训练时一致（默认 v2；也可用环境变量 version 指定）")
    parser.add_argument("--device", type=str, default="auto", choices=["auto", "cuda", "cpu"],
                        help="推理设备 (默认 auto: 有 CUDA 用 cuda，否则回退 cpu)")
    parser.add_argument("--ssl-certfile", type=str, default=None, help="HTTPS 证书文件路径")
    parser.add_argument("--ssl-keyfile", type=str, default=None, help="HTTPS 密钥文件路径")
    parser.add_argument("--allowed-ips", type=str, default="", help="IP 白名单，逗号分隔（非空时仅允许白名单内 IP 调用 /tts /play）")
    parser.add_argument("--blocked-ips", type=str, default="", help="IP 黑名单，逗号分隔")
    parser.add_argument("--cors-origins", type=str, default="", help="CORS 允许域名，逗号分隔（默认仅同源；填 * 允许所有）")
    parser.add_argument("--max-text-length", type=int, default=500, help="文本最大长度限制 (默认 500)")
    parser.add_argument("--auth-token", type=str, default="", help="音频文件访问鉴权令牌（非空时 /audio 需 ?token=）")
    parser.add_argument("--webhook-url", type=str, default="", help="告警 Webhook URL（限流/错误/违禁时通知）")
    parser.add_argument("--require-referer", action="store_true", help="要求 TTS 请求必须带 Referer 头")
    parser.add_argument("--switch-auth-token", type=str, default="", help="音色切换鉴权令牌（非空时切换音色接口需 ?token=）")
    parser.add_argument("--switch-min-interval", type=int, default=0, help="两次成功切换的最小间隔秒数（0=不限）")
    return parser


def apply_args(parsed_args: argparse.Namespace) -> None:
    """把命令行参数写入模块级可变设置。"""
    global ALLOWED_IPS, BLOCKED_IPS, CORS_ORIGINS, MAX_TEXT_LENGTH
    global AUDIO_AUTH_TOKEN, WEBHOOK_URL, REQUIRE_REFERER
    global SWITCH_AUTH_TOKEN, SWITCH_MIN_INTERVAL
    if parsed_args.allowed_ips:
        ALLOWED_IPS = {ip.strip() for ip in parsed_args.allowed_ips.split(",") if ip.strip()}
    if parsed_args.blocked_ips:
        BLOCKED_IPS = {ip.strip() for ip in parsed_args.blocked_ips.split(",") if ip.strip()}
    if parsed_args.cors_origins == "*":
        CORS_ORIGINS = ["*"]
    elif parsed_args.cors_origins:
        CORS_ORIGINS = [o.strip() for o in parsed_args.cors_origins.split(",") if o.strip()]
    MAX_TEXT_LENGTH = parsed_args.max_text_length
    AUDIO_AUTH_TOKEN = parsed_args.auth_token
    WEBHOOK_URL = parsed_args.webhook_url
    REQUIRE_REFERER = parsed_args.require_referer
    SWITCH_AUTH_TOKEN = parsed_args.switch_auth_token
    SWITCH_MIN_INTERVAL = max(0, int(parsed_args.switch_min_interval))


parser = build_parser()
args = parser.parse_args()
apply_args(args)

# 监听地址（与原版一致: 字符串 "None" 视为 None）
HOST = None if args.bind_addr == "None" else args.bind_addr
PORT = args.port
SSL_CERTFILE = args.ssl_certfile
SSL_KEYFILE = args.ssl_keyfile
# 模型版本与推理设备
VERSION = args.version
DEVICE = args.device
