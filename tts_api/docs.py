"""
FastAPI 文档内容与主题
======================
- API_DESCRIPTION: /docs 页头的接口说明 Markdown
- SWAGGER_UI_PARAMETERS: SwaggerUIBundle 配置（不含 CSS，样式由主题文件提供）
- SWAGGER_CSS_URL: 自定义主题地址
  （主题配色与「语音合成台」前台 frontend/index.html 一致）
"""

API_TITLE = "GPT-SoVITS 语音合成 API（语音合成台）"
API_VERSION = "1.3"
SWAGGER_CSS_URL = "/assets/swagger_theme.css"

CONTACT = {
    "name": "GPT-SoVITS",
    "url": "https://github.com/RVC-Boss/GPT-SoVITS",
}

LICENSE_INFO = {
    "name": "MIT License",
    "url": "https://opensource.org/licenses/MIT",
}

API_DESCRIPTION = (
    "**轻量自托管语音合成服务**：选音色 → 提交文本 → 一键合成，支持多语种、流式输出与 AI 语音对话（测试版）。\n\n"
    "朋友试玩请直接打开 **`<服务地址>/`** 进入「语音合成台」前台，手机可用，无需了解本页接口。\n\n"
    "---\n\n"
    "## 快速开始\n\n"
    "**开发者调用只需三步：**\n\n"
    "1. `GET /models` 查看可用音色、模型与参考音频\n"
    "2. 需要换音色时调用 `GET /set_voice`（SoVITS+GPT 一次切换，或分别调用 `GET /set_gpt_weights`、`GET /set_sovits_weights`）。切换为异步排队："
    "会等切换请求提交前的全部任务（含排队中的）结束后生效，期间新提交的任务用新音色；"
    "轮询 `GET /switch_status` 可查看待切换进度（操作人 / 剩余任务 / 预计秒数）\n"
    "3. `POST /tts` 提交 JSON → 202 返回 `task_id`，轮询 `GET /task_status/{task_id}` 直到 `status=done`，响应中的 `play_url` / `download_url` 即试听与下载地址（前台页面已自动完成此流程）\n\n"
    "命令行快速试听：\n\n"
    "```\n"
    "GET /tts?text=你好呀&text_lang=zh\n"
    "```\n\n"
    "---\n\n"
    "## 音色库约定\n\n"
    "每个音色对应 `GPT-SoVITS-API/voices/` 下的一个子目录，放入该音色的模型与参考音频即可：\n\n"
    "```\n"
    "voices/\n"
    "  我的音色/\n"
    "    我的音色-e15.ckpt     # GPT 模型（多个时默认取最高 epoch）\n"
    "    我的音色_e8_s184.pth  # SoVITS 模型\n"
    "    参考音频.wav          # 参考音频\n"
    "    persona.txt           # 可选：AI 对话默认人设\n"
    "  其他音色/ ...\n"
    "```\n\n"
    "新音色放入后无需重启，前台点「刷新模型列表」或重新请求 `/models` 即可出现。\n\n"
    "---\n\n"
    "## 接口列表\n\n"
    "| 方法 | 路径 | 说明 |\n"
    "|------|------|------|\n"
    "| GET  | `/`     | **语音合成台前台**（选音色 + 一键合成，推荐朋友使用） |\n"
    "| POST | `/tts` | 提交 JSON，非流式返回 202 任务 ID（轮询取结果）；流式模式返回音频流 |\n"
    "| GET  | `/task_status/{task_id}` | 轮询任务状态：排队位置 / 预计秒数 / 结果链接 |\n"
    "| GET  | `/tts` | 查询参数调用，同步等待并直接返回音频；流式模式返回音频流 |\n"
    "| GET  | `/play`| 浏览器打开在线试听；流式模式返回音频流 |\n"
    "| GET  | `/chat` | **AI 语音对话前台（测试版）**：文字聊天 + 语音回复，自带 API Key |\n"
    "| POST | `/chat` | AI 对话（测试版）：服务端中转调用你填写的模型 API，回复交给语音合成 |\n"
    "| POST | `/chat/test` | 测试模型接口连通性（测试版） |\n"
    "| POST | `/chat/models` | 自动获取可用模型列表（测试版）：GET {base_url}/models，前台「获取列表」按钮使用 |\n"
    "| GET  | `/persona` | 读取音色目录下的 persona.txt 默认人设（测试版） |\n"
    "| GET  | `/models` | 扫描并列出全部可用音色、模型与参考音频 |\n"
    "| GET  | `/set_voice` | 切换音色（SoVITS+GPT 一次完成，202 异步受理；支持 operator/force/token 参数） |\n"
    "| GET  | `/set_gpt_weights` | 热切换 GPT 模型（202 异步受理，路径须来自 /models） |\n"
    "| GET  | `/set_sovits_weights` | 热切换 SoVITS 模型（202 异步受理，路径须来自 /models） |\n"
    "| GET  | `/switch_status` | 音色切换状态：当前音色 / 待切换（操作人、剩余任务、预计秒数、阶段） |\n"
    "| GET  | `/health` | 服务健康检查与当前模型 |\n"
    "| GET  | `/config` | 查看模型配置 |\n"
    "| POST | `/feedback` | 提交意见反馈（前台页脚「意见反馈」也可提交） |\n\n"
    "---\n\n"
    "## 通用参数\n\n"
    "| 参数 | 说明 |\n"
    "|------|------|\n"
    "| `text_lang` | 合成文本语种；AI 对话（`/chat`）中留空时默认使用参考音频语种 |\n"
    "| `prompt_lang` | 参考音频语种 |\n"
    "| `speed_factor` | 语速倍数，建议 **0.8~1.5**，超出范围音质可能下降 |\n"
    "| `media_type` | 输出音频格式：`wav` / `raw` / `ogg` / `aac`（`aac` 需服务端安装 ffmpeg） |\n"
    "| `ref_audio_path` | 请使用 `/models` 返回的路径（服务端会校验，路径穿越一律拒绝） |\n"
    "| `prompt_text` | 参考音频对应文本；留空按零样本合成，填写准确文本效果更好 |\n"
    "| `gpt_path` / `sovits_path` | （可选）指定本次合成使用的模型路径（来自 `/models`）；多人共用时自动排队切换，不影响他人界面 |\n"
    "| `streaming_mode` | 流式模式，见下方说明 |\n\n"
    "### 支持语言\n\n"
    "`auto`（自动识别）· `ja`（日语）· `zh`（中文）· `en`（英文）· `yue`（粤语）· `ko`（韩语）"
    "及各语种纯语言模式（`all_zh` / `all_ja` 等）。\n\n"
    "> 每个音色的适配语言取决于其训练数据，具体以该音色的说明为准。\n\n"
    "---\n\n"
    "## 流式模式说明\n\n"
    "| 值 | 行为 |\n"
    "|----|------|\n"
    "| `0` / `false` | 完整合成后返回（推荐，音质最佳） |\n"
    "| `1` | 分段返回（旧版流式，音质最佳但响应最慢） |\n"
    "| `2` | 流式模式（中等质量、中等速度） |\n"
    "| `3` | 固定长度块模式（响应最快但质量较低） |\n\n"
    "注意：流式模式下 `media_type` 仅支持 `wav` 和 `raw`，`ogg`/`aac` 会缓冲后分块发送。\n\n"
    "---\n\n"
    "## 频率限制与排队\n\n"
    "- 每 IP 每分钟 **40 次** · 全局限量 **100 次/分钟** · AI 对话每 IP 每分钟 **10 轮**\n"
    "- 最大并发 **2** · 排队上限 **50** · 临时文件与任务状态保留 **1 小时**\n"
    "- 排队中的任务可轮询 `/task_status/{task_id}` 查看「当前排队第 X 个，预计 X 秒」\n"
    "- 文本需通过安全检测\n\n"
    "---\n\n"
    "## 错误码\n\n"
    "| 状态码 | 含义 | 解决方法 |\n"
    "|--------|------|----------|\n"
    "| 200 | 成功 | — |\n"
    "| 202 | 已受理（排队合成中） | 轮询 `/task_status/{task_id}` 获取结果 |\n"
    "| 400 | 参数错误 / 合成失败 / 内容违规 / 模型未加载 | 检查请求参数、文本长度和内容；模型未加载时先在前台选音色 |\n"
    "| 403 | 令牌无效 | `/audio` 需要 `?token=`、或切换音色需要 `?token=`（配置了对应令牌时） |\n"
    "| 404 | 音频文件不存在 | 临时文件已过期（保留 1 小时），请重新合成 |\n"
    "| 413 | 请求体过大 | 单次请求不超过 10MB |\n"
    "| 429 | 请求过于频繁 | 每 IP 每分钟最多 40 次，请稍后再试 |\n"
    "| 502 | 模型服务调用失败（AI 对话） | 检查 Base URL 与 API Key 是否有效 |\n"
    "| 503 | 服务繁忙 | 全局限量 100 次/分钟已用尽，请稍后重试 |\n\n"
    "---\n\n"
    "## 意见反馈\n\n"
    "有任何建议、问题或想新增的音色，可通过 **前台页脚「意见反馈」** 或调用 **`POST /feedback`** 提交，反馈将保存在服务端供管理员查看。\n\n"
    "---\n\n"
    "## 免责声明\n\n"
    "本服务仅限个人学习交流试用，不提供 SLA 保障，请勿合成违法或冒充他人等内容。管理员有权在不通知的情况下停止服务。\n\n"
    "> 基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 构建，MIT 许可"
)

SWAGGER_UI_PARAMETERS = {
    "defaultModelsExpandDepth": -1,
    "docExpansion": "list",
    "filter": True,
    "displayRequestDuration": True,
}
