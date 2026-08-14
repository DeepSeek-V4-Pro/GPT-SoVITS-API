# tts_api —— 语音合成台 API（模块化架构）

GPT-SoVITS 语音合成台 API 的服务实现包。按职责拆分为 20+ 个模块（单向依赖、无循环），
前端页面随包分发（`frontend/`），服务只读取包内副本。

启动方式（把本项目放进 GPT-SoVITS 仓库根目录后）:

    python GPT-SoVITS-API/api.py -a 0.0.0.0 -p 9880   # 仓库根目录启动（推荐）
    cd GPT-SoVITS-API && python api.py                # 或: python -m tts_api

## 模块结构

```
tts_api/
├── __init__.py        包说明（轻量，不触发模型加载）
├── __main__.py        python -m tts_api 入口
├── main.py            启动入口（uvicorn）
├── paths.py           仓库根目录自动定位 / sys.path / 数据与前端文件路径
├── config.py          全部可调常量 + 命令行参数解析
├── logging_setup.py   日志（控制台 + 按天滚动文件）
├── docs.py            /docs 页面 Markdown 描述与 Swagger 样式
├── security.py        限流器、SSRF 防护、路径/文本校验、告警、IP 工具
├── llm.py             AI 对话基础（LLM 调用、persona、回复清洗）
├── voice_library.py   音色库扫描与模型路径校验
├── engine.py          TTS 引擎初始化、音色代际守卫（合成/切换协调）、并发信号量
├── audio.py           音频编码（wav/ogg/aac/raw、流式 WAV 头）
├── schemas.py         Pydantic 请求/响应模型
├── validation.py      请求校验与流式参数解析
├── synth.py           TTS 核心合成（完整/流式）
├── tasks.py           异步任务队列（202 轮询）
├── middleware.py      HTTP 安全中间件
├── app.py             FastAPI 应用装配（CORS/静态/中间件/路由/lifespan）
├── routers/
│   ├── system.py      /health /models /config /feedback
│   ├── models.py      /set_voice /set_gpt_weights /set_sovits_weights /switch_status
│   ├── tts.py         /tts /task_status /audio /play 与前台首页
│   └── chat.py        /chat /chat/test /persona（测试版）
└── frontend/          前端副本（index.html / chat.html / debug_audio.html / assets，
                       assets 内 swagger_theme.css 为 /docs 文档页主题）
```

## 音色切换协调（多人共用防“打架”）

切换走**异步排队**语义，核心在 `engine.ModelSwitchGuard`：

- 每条合成任务在**提交时刻**绑定音色代际（epoch）：之后无论排队多久、
  发生多少次切换，任务都按提交时的音色合成；
- 音色切换会等待**切换请求提交前**的全部任务（含排队中的）结束后才生效，
  不会把别人排队中的任务换成新音色；
- 切换排队期间的新任务绑定新代际，等新音色加载完成后才合成；
- 切换排队期间再来新请求 → **合并覆盖**（last-writer-wins），最终只加载一次模型；
  目标与当前音色一致则直接取消/成功返回；
- `GET /switch_status` 展示当前音色、待切换（操作人/剩余任务/预计秒数/阶段），
  前端首页与聊天页顶部横幅轮询展示；
- 可选 `operator` 昵称记录操作人；`?force=1` 紧急切换（只等运行中任务，
  排队任务改随新音色）；配置 `SWITCH_AUTH_TOKEN` 可限制仅持令牌者切换。

### 每任务指定音色（多人各自选音色，互不覆盖）

- `POST /tts`、`GET /tts`、`/play`、`POST /chat` 可携带 `gpt_path` / `sovits_path`
  （来自 `/models`），声明**本次合成**使用的音色；
- 服务端把任务绑定到该音色的代际：有人正在切换其他音色时，本任务会排队等自己的
  音色加载（必要时自动补一次切回切换），**绝不拿错音色合成**；
- 各用户页面只显示自己选择的音色，不会因为别人切换而刷新；状态栏会提示
  “服务端当前为 X，提交任务将自动排队切换”；
- 启用 `SWITCH_AUTH_TOKEN` 时，合成请求不代切音色（防止绕过切换鉴权）。

## AI 对话（测试版）扩展

- 上下文：最多携带最近 **20 轮**对话（每条截断 1000 字符）；
- 聊天记录：用户可开启「保存聊天记录到本浏览器」（IndexedDB，纯本地不上传），
  **按「音色 + 模型名」分区**保存，切换音色/模型时自动加载对应记录；
  支持导出全部记录为 Markdown 文件、**导入**（自动识别标签与时间、重复跳过）与清空
  （「清空对话」只清当前分区，「清空已存记录」清全部；旧的无标签记录保持未分区不丢失）；
- 长期记忆：发消息时前端在全部已存记录中做关键词检索，最相关片段经
  `memory_hints` 注入上下文，模型可回忆更早的历史内容。

## 移动端播放兼容（已修复）

- `/audio` 始终返回 200 完整内容（不处理 Range、不带 Content-Disposition），
  兼容安卓 WebView / 国产浏览器内核对 206 分部响应处理不佳的问题；
- 前端播放与下载统一走 fetch + Blob，绕开部分 OEM 浏览器对直链媒体的原生播放器拦截；
- wav 输出显式 16-bit PCM；iPhone 自动禁用 ogg 选项。

## 依赖关系（单向，无循环）

```
paths → config → logging_setup → security → llm / voice_library
                                    └──────→ engine → validation → synth → tasks
schemas / audio / docs 为纯工具模块
routers/* 依赖上述业务模块；app.py 装配全部路由；main.py 启动
```

- 可变设置（`ALLOWED_IPS`、`MAX_TEXT_LENGTH`、`AUDIO_AUTH_TOKEN` 等）一律通过
  `config.属性` 访问，避免 `from ... import` 拿到旧值。
- `/models` 的 `current` 字段由 system 路由从 `engine.MODEL_CFG` 组装，
  避免 voice_library ↔ engine 循环依赖。

## 修改指南

- 改启动参数 / 阈值 → `config.py`
- 加安全规则 → `security.py` / `middleware.py`
- 加合成参数校验 → `validation.py` / `schemas.py`
- 加接口 → 对应 `routers/*.py`（新领域则新建 router 并在 `app.py` 注册）
- 改前端 → `frontend/`（`/assets` 挂载自 `frontend/assets/`）

## 启动

```
cd <你的 GPT-SoVITS 根目录>
python GPT-SoVITS-API/api.py -a 0.0.0.0 -p 9880
```

`paths.py` 会自动从工作目录向上定位 GPT-SoVITS 仓库根目录并 `chdir` 过去，
因此可以在仓库内任意位置启动；数据目录（logs / feedback / temp_audio）位于本项目目录下。
