"""
API 请求 / 响应模型（Pydantic）
================================
集中定义全部请求体与响应模型，字段、默认值与校验规则见各 Field 说明。
"""

from typing import Union

from pydantic import BaseModel, Field

from . import config


class TTSRequest(BaseModel):
    """语音合成请求体"""
    text: str = Field(description="要合成语音的文本内容，建议 5~500 字符")
    text_lang: str = Field(description="文本语言代码: auto(自动识别) / ja(日语) / zh(中文) / en(英文) / yue(粤语) / ko(韩语)")
    ref_audio_path: str = Field(default=config.DEFAULT_REF_AUDIO_PATH, description="参考音频文件的完整路径，请使用 GET /models 返回的路径（服务端会做路径校验）")
    aux_ref_audio_paths: list = Field(default=None, description="辅助参考音频路径列表（可选）")
    gpt_path: str = Field(default="", description="指定本次合成使用的 GPT(Text2Semantic) 模型路径（可选，来自 /models；填写后服务端自动排队切换到该音色，不会改变其他用户界面）")
    sovits_path: str = Field(default="", description="指定本次合成使用的 SoVITS 模型路径（可选，来自 /models）")
    prompt_lang: str = Field(default=config.DEFAULT_PROMPT_LANG, description="参考音频的语言代码")
    prompt_text: str = Field(default=config.DEFAULT_PROMPT_TEXT, description="参考音频对应的文本，建议填写以提高效果")
    top_k: int = Field(default=15, description="Top-K 采样参数", ge=1)
    top_p: float = Field(default=1, description="Top-P 核采样参数", ge=0, le=1)
    temperature: float = Field(default=0.6, description="温度参数，越高越随机", ge=0.1, le=2.0)
    text_split_method: str = Field(default="cut0", description="文本切分方式")
    batch_size: int = Field(default=20, description="并行推理批次大小", ge=1)
    batch_threshold: float = Field(default=0.75, description="批次分割阈值")
    split_bucket: bool = Field(default=True, description="是否启用分桶处理")
    speed_factor: float = Field(default=1.0, description="语速倍数，建议 0.8~1.5（超出范围音质可能下降）", ge=0.5, le=2.0)
    fragment_interval: float = Field(default=0.3, description="片段间隔（秒）")
    seed: int = Field(default=-1, description="随机种子（-1 为完全随机）")
    media_type: str = Field(default="wav", description="输出音频格式: wav / raw / ogg / aac")
    parallel_infer: bool = Field(default=True, description="是否并行推理")
    repetition_penalty: float = Field(default=1.35, description="重复惩罚系数")
    sample_steps: int = Field(default=32, description="采样步数", ge=1)
    super_sampling: bool = Field(default=False, description="是否启用超采样")
    streaming_mode: Union[bool, int] = Field(default=False, description="流式模式: 0/false=关闭(完整合成), 1=分段返回(音质最佳最慢), 2=流式(中等质量/速度), 3=固定长度块(最快但质量较低)")
    overlap_length: int = Field(default=2, description="流式模式重叠长度", ge=0)
    min_chunk_length: int = Field(default=16, description="流式模式最小 Token 长度", ge=1)

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "text": "こんにちは、元気ですか？",
                    "text_lang": "ja",
                    "ref_audio_path": config.DEFAULT_REF_AUDIO_PATH,
                    "prompt_lang": config.DEFAULT_PROMPT_LANG,
                    "prompt_text": config.DEFAULT_PROMPT_TEXT,
                    "speed_factor": 1.0,
                    "streaming_mode": False,
                },
                {
                    "text": "欢迎使用语音合成服务，今天天气真不错。",
                    "text_lang": "zh",
                    "ref_audio_path": config.DEFAULT_REF_AUDIO_PATH,
                    "prompt_lang": config.DEFAULT_PROMPT_LANG,
                    "prompt_text": config.DEFAULT_PROMPT_TEXT,
                    "speed_factor": 1.0,
                    "streaming_mode": False,
                },
            ]
        }


class TTSTaskInfo(BaseModel):
    """合成任务状态：POST /tts 的 202 响应与 GET /task_status/{task_id} 的轮询响应"""
    task_id: str = Field(description="任务 ID")
    status: str = Field(description="任务状态: queued(排队中) / running(合成中) / done(完成) / error(失败)")
    status_url: Union[str, None] = Field(default=None, description="状态轮询地址（仅 POST /tts 响应返回）")
    queue_position: int = Field(default=0, description="当前排队位置（0 = 即将/正在合成）")
    queue_length: int = Field(default=0, description="当前排队 + 运行中的任务总数")
    estimated_seconds: float = Field(default=0, description="预计还需等待秒数（估算）")
    elapsed_seconds: float = Field(default=0, description="已等待/已耗时秒数")
    tip: str = Field(default="", description="人类可读的状态提示")
    play_url: Union[str, None] = Field(default=None, description="完成后可用的在线播放地址")
    download_url: Union[str, None] = Field(default=None, description="完成后可用的下载地址")
    error: Union[str, None] = Field(default=None, description="失败原因（status=error 时）")
    voice: Union[dict, None] = Field(default=None, description="任务在提交时刻绑定的音色（sovits_name/gpt_name/epoch）：多人切换音色时，本任务仍按提交时的音色合成")


class FeedbackRequest(BaseModel):
    """意见反馈请求体"""
    text: str = Field(description="反馈内容（必填：建议、问题或新音色请求）", min_length=1, max_length=2000)
    contact: str = Field(default="", description="联系方式（可选，方便回复）", max_length=200)
    page: str = Field(default="", description="来源页面（可选）", max_length=300)


class ChatRequest(BaseModel):
    """AI 语音对话请求（测试版）: 用户自带 OpenAI 兼容 API Key"""
    text: str = Field(description="用户输入的消息", min_length=1, max_length=2000)
    base_url: str = Field(description="OpenAI 兼容接口地址，如 https://api.deepseek.com（服务端仅中转，不保存）")
    api_key: str = Field(description="用户自备的 API Key（仅本次请求使用，服务端不保存不记录）")
    model: str = Field(default="deepseek-v4-pro", description="模型名称")
    system_prompt: str = Field(default="", max_length=2000, description="系统提示词（人设），留空使用默认人设")
    history: list = Field(default=None, description="历史对话 [{role: user/assistant, content}]，最多保留最近 20 轮")
    memory_hints: list = Field(default=None, description="从用户浏览器保存的全部聊天记录中检索到的相关片段 "
                             "[{role, content, time}]，服务端注入上下文供模型参考；最多 8 条、每条截断 200 字")
    max_tokens: int = Field(default=config.CHAT_MAX_TOKENS, ge=64, le=4096, description="模型回复最大 token 数")
    temperature: float = Field(default=0.8, ge=0, le=2, description="采样温度")
    ref_audio_path: str = Field(default=config.DEFAULT_REF_AUDIO_PATH, description="参考音频路径（来自 /models）")
    prompt_text: str = Field(default=config.DEFAULT_PROMPT_TEXT, description="参考音频对应文本")
    prompt_lang: str = Field(default=config.DEFAULT_PROMPT_LANG, description="参考音频语言")
    text_lang: str = Field(default="", description="合成文本的语言代码（auto/ja/zh/en/yue/ko 等），留空时默认使用参考音频语言 prompt_lang")
    speed_factor: float = Field(default=1.0, ge=0.5, le=2.0, description="语速倍数")
    media_type: str = Field(default="wav", description="输出音频格式: wav / ogg / aac / raw")
    gpt_path: str = Field(default="", description="指定本次合成使用的 GPT 模型路径（可选，来自 /models；填写后服务端自动排队切换到该音色）")
    sovits_path: str = Field(default="", description="指定本次合成使用的 SoVITS 模型路径（可选，来自 /models）")


class ChatTestRequest(BaseModel):
    """测试模型接口连通性（测试版）"""
    base_url: str = Field(description="OpenAI 兼容接口地址")
    api_key: str = Field(description="用户自备的 API Key")
    model: str = Field(default="deepseek-v4-pro", description="模型名称")


class ChatModelsRequest(BaseModel):
    """自动获取可用模型列表（测试版）: GET {base_url}/models"""
    base_url: str = Field(description="OpenAI 兼容接口地址，如 https://api.deepseek.com（服务端仅中转，不保存）")
    api_key: str = Field(description="用户自备的 API Key（仅本次请求使用，服务端不保存不记录）")
