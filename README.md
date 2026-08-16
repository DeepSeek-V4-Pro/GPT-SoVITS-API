# GPT-SoVITS-API · 语音合成台 API

> 作者与仓库：[DeepSeek-V4-Pro/GPT-SoVITS-API](https://github.com/DeepSeek-V4-Pro/GPT-SoVITS-API) ｜ 版本 **1.5**（[更新日志](./CHANGELOG.md)）

基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 引擎的轻量自托管语音合成 HTTP 服务。
放进 GPT-SoVITS 仓库根目录、加入音色模型即可使用：**选音色 → 提交文本 → 一键合成**，
支持多语种、流式输出、模型热切换与 AI 语音对话（测试版），自带手机友好的 Web 前台与 Swagger 接口文档。

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [界面一览](#界面一览)
- [音色与人设](#音色与人设)
- [目录结构](#目录结构)
- [启动参数](#启动参数)
- [API 速览](#api-速览)
- [常见问题](#常见问题)
- [公网部署安全建议](#公网部署安全建议)
- [注意事项](#注意事项)
- [免责声明](#免责声明)

## 功能特性

| 类别 | 说明 |
|------|------|
| **语音合成台前台** | 音色下拉自选、参考音频自动填充、任务排队进度、最近 12 条合成记录 |
| **模型热切换** | `/set_voice` 一次切换 SoVITS+GPT；异步排队等待前方任务跑完才生效，多人切换自动合并防「打架」；每个请求可携带 `gpt_path` / `sovits_path` 指定本次音色，各用户界面互不覆盖 |
| **多参考音频** | 1 主 + N 副参考音频（前台多选）；参考音频以「文件名 = 台词文本」命名时自动回填参考文本，复刻效果最佳 |
| **AI 语音对话（测试版）** | 使用你自己的 OpenAI 兼容 API Key，服务端仅中转不保存；上下文 20 轮；聊天记录按「音色 + 模型」分区存浏览器（IndexedDB），支持导出 / 导入 / 长期记忆检索；模型列表自动获取 |
| **异步任务队列** | `POST /tts` 提交即返回 202，轮询 `/task_status/{task_id}` 取结果 |
| **流式合成** | `streaming_mode` 1/2/3 分段输出 |
| **多格式输出** | wav / ogg / raw / aac（aac 需 ffmpeg；wav 为 16-bit PCM，手机兼容性最佳） |
| **合成台公告弹窗** | 进入前台自动展示公告（默认：使用引导 + 注意事项 + 免责声明指引）；可用 `tts_api/frontend/notice.md` 覆盖（首行为标题、空一行后为正文，支持 `[文字](链接)`）；已读状态按内容哈希存浏览器，公告更新后自动重弹 |
| **移动端播放兼容** | 音频直链始终返回完整 200，前端播放/下载统一走 fetch + Blob，修复安卓 WebView / 国产浏览器无法播放问题 |
| **安全防护** | IP 黑白名单、限流（仅重资源端点）、路径穿越校验、SSRF 防护、音频访问令牌、可选音色切换令牌、HTTPS 支持 |

## 快速开始

### 1. 放入仓库

把整个 `GPT-SoVITS-API/` 文件夹放进你的 GPT-SoVITS 仓库**根目录**：

```
<你的 GPT-SoVITS 根目录>/
├── GPT_SoVITS/
├── GPT_weights/、SoVITS_weights/ ...
└── GPT-SoVITS-API/          ← 本项目
```

### 2. 自动配置与安装依赖（推荐）

```
python GPT-SoVITS-API/install_deps.py
```

Windows 也可以直接双击 `start.bat`（首次运行自动检测并安装缺失依赖）。脚本依次完成：
定位仓库根 → 检查 Python / PyTorch / CUDA → 安装本项目额外依赖 → 检查预训练模型与 ffmpeg →
扫描 `voices/` 音色库 → 把 Python 解释器路径写入启动脚本。

| 参数 | 说明 |
|------|------|
| `--check` | 只体检并输出报告，不安装、不改文件 |
| `-y` / `--yes` | 全自动模式，不再询问 |
| `--no-install` | 只配置启动脚本，不安装依赖 |
| `--root <路径>` | 手动指定 GPT-SoVITS 仓库根目录 |

> 注意：脚本只安装「本项目」的少量额外依赖；GPT-SoVITS 本体环境（torch 等）请先按官方教程安装。

### 3. 加入音色

把每个音色的模型与参考音频放进 `GPT-SoVITS-API/voices/` 下的同名子目录（详见 [voices/README.md](./voices/README.md)）：

```
voices/
  我的音色/
    我的音色-e15.ckpt     # GPT 模型（多个时默认取 epoch 最高的）
    我的音色_e8_s184.pth  # SoVITS 模型（多个时默认取最高的）
    参考音频.wav          # 参考音频（可多条；文件名即台词文本时效果最佳）
    persona.toml          # 可选：AI 对话人设（详见下文）
```

- 服务启动时自动加载扫描到的第一组可用模型；放入新音色**无需重启**，前台点「刷新模型列表」即可；
- 仓库根目录下的 `GPT_weights*/SoVITS_weights*` 也会被一并扫描。

### 4. 启动

```
cd <你的 GPT-SoVITS 根目录>
python GPT-SoVITS-API/api.py -a 0.0.0.0 -p 9880
```

Windows 也可直接双击 `start.bat`；Linux / macOS 用 `bash GPT-SoVITS-API/start.sh`
（可用环境变量 `PYTHON_EXE` 指定解释器）。进入项目目录后启动同样可以（自动定位仓库根目录）。

### 5. 使用

| 入口 | 地址 |
|------|------|
| 语音合成台前台 | http://127.0.0.1:9880/ |
| AI 语音对话（测试版） | http://127.0.0.1:9880/chat |
| API 文档（Swagger） | http://127.0.0.1:9880/docs |
| 健康检查 | http://127.0.0.1:9880/health |

## 界面一览

- **下拉选择框（自定义组件）**：合成台与聊天页全部下拉框为自绘主题弹层——圆角白底、分组标题、
  禁用项灰显、当前项高亮 + ✓ 标记；支持键盘 ↑↓ / Enter / Esc、点击外部关闭；
  空间不足自动向上展开、超高内部滚动；原生 select 仅作数据源，所有既有逻辑零改动。
- **聊天页可折叠面板**：模型接口 / 系统提示词（人设）/ 聊天记录 / 音色与参考音频 /
  参考音频高级选项五组可独立折叠，常用项默认展开、高级项默认收起，状态记忆在浏览器本地。

## 音色与人设

每个音色目录可放一个人设文件，AI 对话的系统提示词留空时自动加载并跟随音色切换。
查找优先级 **`persona.toml` → `persona.json` → 旧版 `persona.txt`**（纯文本直接作为完整提示词）。

`persona.toml` 结构化分区示例（空字段分区自动省略，渲染为带分隔线的系统提示词）：

```toml
[personality]       # 身份设定
name = "小盐"
title = "面包师傅"
identity = """你是谁、你的世界是怎样的…"""
background = """你的经历…"""
character = """你的性格…"""
relationship = """你如何称呼对方…"""

[behavior]          # 行为准则
behavior_style = """如何接话…"""
reply_style = """说话风格…"""

[chat]              # 聊天注意事项
rules = """该聊天中的注意事项…"""

[output]            # 输出格式要求
requirements = """回复会交给语音合成朗读时的约束…"""
```

> TOML 解析自动兼容 `tomllib`（Python 3.11+）/ `tomli` / `tomlkit`；完整字段说明见 [voices/README.md](./voices/README.md)。

## 目录结构

```
GPT-SoVITS-API/
├── api.py              启动入口
├── install_deps.py     一键配置 / 依赖安装 / 环境体检
├── start.bat / start.sh  一键启动（Windows / Linux-macOS）
├── requirements.txt    额外依赖
├── README.md / CHANGELOG.md / LICENSE
├── tts_api/            服务实现包
│   ├── main.py         启动（uvicorn）
│   ├── paths.py        路径与工作目录初始化（自动定位 GPT-SoVITS 仓库根）
│   ├── config.py       全部可调配置 + 命令行参数
│   ├── engine.py       TTS 引擎初始化 / 音色代际守卫 / 并发控制
│   ├── synth.py        核心合成（完整 / 流式）
│   ├── tasks.py        异步任务队列
│   ├── routers/        system / models / tts / chat 四组路由
│   └── frontend/       前台页面（index / chat / debug_audio + assets）
└── voices/             音色库（每个子目录一个音色）
```

模块架构、音色切换协调与 AI 对话扩展的详细说明见 [tts_api/README.md](./tts_api/README.md)。

## 启动参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `-a` / `--bind_addr` | `0.0.0.0` | 绑定地址 |
| `-p` / `--port` | `9880` | 端口 |
| `--version` | `v2`（或环境变量 `version`） | 模型版本 v1/v2/v3/v4/v2Pro/v2ProPlus，须与训练时一致 |
| `--device` | `auto` | auto / cuda / cpu |
| `--auth-token` | 空 | 非空时 `/audio` 需要 `?token=` |
| `--allowed-ips` | 空 | IP 白名单（逗号分隔），仅限非本地请求的重资源端点 |
| `--blocked-ips` | 空 | IP 黑名单 |
| `--cors-origins` | 空 | CORS 允许域名（`*` 表示全部） |
| `--ssl-certfile` / `--ssl-keyfile` | 空 | HTTPS 证书 |
| `--webhook-url` | 空 | 告警 Webhook（限流 / 违禁 / 异常时通知） |
| `--max-text-length` | `500` | 文本长度上限（超出截断） |
| `--require-referer` | 关 | 要求 `/tts`、`/play` 携带 Referer |
| `--switch-auth-token` | 空 | 音色切换鉴权令牌（多人共用时可选） |
| `--switch-min-interval` | `0` | 两次成功切换的最小间隔秒数（0=不限） |

数据目录（首次启动自动创建，均在本项目目录下）：`logs/`、`feedback/`、`temp_audio/`
（合成临时音频保留 1 小时自动清理；`logs`、`feedback` 可用环境变量 `TTS_API_LOG_DIR`、`TTS_API_FEEDBACK_DIR` 覆盖）。

需要自行修改的占位符：`start.bat` / `start.sh` 中的 `PYTHON_EXE`（运行 `install_deps.py` 自动替换）、
`tts_api/config.py` 中的默认参考音频 / 模型路径与 `EXTRA_MODEL_DIRS` / `REF_SEARCH_DIRS`。

## API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 语音合成台前台 |
| POST | `/tts` | 提交合成任务（202 + task_id，轮询取结果；流式模式直接返回音频流） |
| GET | `/tts` | 查询参数直接合成（curl 友好） |
| GET | `/task_status/{task_id}` | 任务状态轮询 |
| GET | `/play` | 浏览器在线试听 |
| GET | `/models` | 音色 / 模型 / 参考音频列表 |
| GET | `/set_voice` | 切换音色（SoVITS+GPT 一次完成，202 异步受理；支持 operator/force/token） |
| GET | `/set_gpt_weights` · `/set_sovits_weights` | 模型热切换（202 异步受理） |
| GET | `/switch_status` | 音色切换状态（当前音色 / 待切换进度） |
| GET | `/health` · `/config` | 状态与配置 |
| GET | `/notice` | 合成台公告内容（可被 `frontend/notice.md` 覆盖） |
| GET | `/chat` · POST `/chat` · POST `/chat/test` · POST `/chat/models` · GET `/persona` | AI 语音对话（测试版） |
| POST | `/feedback` | 意见反馈 |

完整参数说明见 `/docs`。

## 常见问题

- **启动报错「找不到 GPT_SoVITS」**：确认 `GPT-SoVITS-API` 位于 GPT-SoVITS 仓库根目录下，且该目录含 `GPT_SoVITS` 包。
- **前台提示「未发现模型文件」**：把 `.ckpt` / `.pth` 放进 `voices/` 子目录（或 `GPT_weights*`、`SoVITS_weights*`），点「刷新模型列表」。
- **提示「模型未加载」**：先在前台选择音色并点「加载音色」。
- **CPU 机器**：`python api.py --device cpu`（半精度自动关闭）。
- **端口占用**：用 `-p 端口号` 换端口。
- **aac 报错**：安装 ffmpeg 并加入 PATH。
- **切换音色后仍用旧音色**：切换是异步排队的——会等切换请求提交前已排队的任务全部跑完后才生效，可轮询 `/switch_status` 或看前台顶部横幅了解进度。
- **手机浏览器合成成功但无法播放**：本版本已修复（音频直链返回完整 200 + 前端 fetch+Blob 播放）。如仍异常，可访问 `/debug_audio` 诊断页测试并反馈日志。

## 公网部署安全建议

- 启用 `--auth-token` 保护音频文件直链；
- 用 `--allowed-ips` 限制调用来源，或放在反向代理（nginx / caddy）后并启用 HTTPS（`--ssl-certfile`）；
- AI 对话接口建议仅对内网开放（该接口由访客自带 API Key，服务端仅中转，且内置 SSRF 防护）。

## 注意事项

- **AI 语音对话请勿在不可信部署上输入重要 API Key**。该功能由访客自带 OpenAI 兼容 API Key，
  前端把 Key 保存在浏览器本地（localStorage），服务端仅中转、不保存不记录。
  由于项目开源、任何人都可以二次开发，**经过他人改造的版本可能被植入窃取密钥的代码**
  （例如把前端填写的 Key 回传到第三方服务器）。请只在自己部署、可信任的服务上使用该功能；
- 建议为 AI 对话使用**专用、限额的子 Key**，避免使用主账号或高额度 Key，并定期在 API 平台检查用量；
- 若对外提供公网服务，请务必阅读上方「公网部署安全建议」并启用相应防护；
- 本项目不内置任何真实 API Key，前端也不会预填密钥，请勿在任何配置文件或代码中提交真实密钥。

## 免责声明

本项目仅供学习、研究与个人合法用途。请勿用于合成违法内容、冒充他人或任何侵犯他人权益的行为。
使用者需自行承担全部责任，并遵守当地法律法规。

**AI 语音对话（测试版）特别提示**：本接口由访客自行填写 API Key，服务端仅中转且不保存。
由于项目开源，作者无法保证第三方改造版本的代码安全；若在不可信部署上使用，存在 API Key
被截获、窃取或滥用的风险。请仅在自建可信服务上使用，并建议使用限额子 Key。
作者不对因使用本项目（含被第三方修改的版本）造成的密钥泄露、资损或其他任何损失承担责任。

## 作者与许可

- 作者：DeepSeek-V4
- 仓库：[DeepSeek-V4-Pro/GPT-SoVITS-API](https://github.com/DeepSeek-V4-Pro/GPT-SoVITS-API)
- 欢迎 Star、Issue 与 PR，使用中遇到问题请先查看文档与常见问题。
- [MIT](./LICENSE)，基于 GPT-SoVITS（MIT License）构建。
