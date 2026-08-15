# GPT-SoVITS-API · 语音合成台 API

> 作者与仓库：[DeepSeek-V4-Pro/GPT-SoVITS-API](https://github.com/DeepSeek-V4-Pro/GPT-SoVITS-API)

基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 引擎的轻量自托管语音合成 HTTP 服务。
把本项目放进 GPT-SoVITS 仓库根目录、加入音色模型后即可使用：选音色 → 提交文本 → 一键合成，
支持多语种、流式输出、模型热切换与 AI 语音对话（测试版），自带手机友好的 Web 前台与 Swagger 接口文档。

## 功能特性

- **开箱即用的语音合成台前台**：音色下拉自选、参考音频自动填充、任务排队进度展示、历史记录
- **模型热切换（异步排队）**：`/set_voice` 一次切换 SoVITS+GPT；切换会等切换请求前已提交的任务
  （含排队中的）全部跑完才生效，不会把别人排队中的任务换成新音色；`/switch_status` 实时展示
  切换进度（操作人 / 剩余任务 / 预计秒数），前端顶部横幅轮询可见；多人切换自动合并，避免“打架”；
  **每个合成请求可携带 `gpt_path` / `sovits_path` 指定本次音色**，各用户界面保持自己的选择、
  不因别人切换而刷新，任务自动排队等自己的音色
- **异步任务队列**：`POST /tts` 提交即返回 202，轮询 `/task_status/{task_id}` 取结果
- **流式合成**：`streaming_mode` 1/2/3 分段输出
- **多格式输出**：wav / ogg / raw / aac（aac 需要 ffmpeg；wav 为 16-bit PCM，手机兼容性最佳）
- **AI 语音对话（测试版）**：自带 OpenAI 兼容 API Key，服务端仅中转、不保存；
  上下文最多 20 轮；聊天记录可由用户选择永久保存至浏览器（IndexedDB）、
  **按「音色 + 模型」分区**管理，支持导出 Markdown 与**导入**（自动识别标签、重复跳过）；
  AI 可从全部历史记录中检索相关内容作为长期记忆；模型名称支持**自动获取可用模型列表**
  （「获取列表」按钮，服务端代理调用各服务商的 `GET /models`，内置预设服务商与推荐模型置顶作为默认推荐）
- **移动端播放兼容**：音频直链始终返回完整 200 响应，前端播放/下载统一走 fetch + Blob，
  修复安卓 WebView / 国产浏览器「合成成功但无法播放」的问题
- **安全防护**：IP 黑白名单、限流（仅重资源端点）、路径穿越校验、SSRF 防护、音频访问令牌、
  可选音色切换令牌、HTTPS 支持

## 目录结构

```
GPT-SoVITS-API/
├── api.py              启动入口
├── install_deps.py     一键配置 / 依赖安装 / 环境体检
├── start.bat           一键启动（Windows）
├── start.sh            一键启动（Linux / macOS）
├── requirements.txt    额外依赖
├── README.md
├── LICENSE             MIT
├── tts_api/            服务实现包
│   ├── main.py         启动（uvicorn）
│   ├── paths.py        路径与工作目录初始化（自动定位 GPT-SoVITS 仓库根）
│   ├── config.py       全部可调配置 + 命令行参数
│   ├── engine.py       TTS 引擎初始化 / 音色代际守卫（合成与切换协调）/ 并发控制
│   ├── synth.py        核心合成（完整 / 流式）
│   ├── tasks.py        异步任务队列
│   ├── routers/        system / models / tts / chat 四组路由
│   └── frontend/       前台页面（index / chat / debug_audio + assets）
└── voices/             音色库（每个子目录一个音色，见下）
```

## 部署要求

1. 已按 GPT-SoVITS 官方教程安装好依赖，官方 WebUI 能正常合成
2. 已下载预训练模型（`chinese-roberta-wwm-ext-large`、`chinese-hubert-base` 等）
3. 已有训练好的音色模型（`.ckpt` + `.pth`），或使用官方预训练模型
4. 推荐 NVIDIA GPU；CPU 也能跑（启动时加 `--device cpu`）
5. aac 输出需系统安装 ffmpeg

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

在仓库根目录执行：

```
python GPT-SoVITS-API/install_deps.py
```

Windows 也可以直接双击 `GPT-SoVITS-API/start.bat` —— 首次运行会自动检测缺失依赖并
自动安装，无需手动配置。脚本会依次完成：

1. 自动定位 GPT-SoVITS 仓库根目录，检查项目摆放位置；
2. 检查 Python 版本与 PyTorch / CUDA 环境；
3. 检测并安装本项目额外依赖（`requirements.txt`，仅少量包）；
4. 检查预训练模型（BERT / HuBERT / 默认权重）与 G2PW、ffmpeg；
5. 扫描 `voices/` 音色库，报告可直接使用的音色；
6. 把当前 Python 解释器自动写入 `start.bat` / `start.sh` 的占位符，
   以后双击 `start.bat` 不再需要关心 Python 路径。

常用参数：

| 参数 | 说明 |
|------|------|
| `--check` | 只体检并输出报告，不安装、不改任何文件 |
| `-y` / `--yes` | 全自动模式，不再询问 |
| `--no-install` | 只配置启动脚本，不安装依赖 |
| `--no-launchers` | 跳过启动脚本自动配置 |
| `--root <路径>` | 手动指定 GPT-SoVITS 仓库根目录 |

> 注意：本脚本只安装「本项目」的少量额外依赖；GPT-SoVITS 本体环境（torch 等）请先按官方
> 教程安装（Windows: `install.ps1`，Linux/macOS: `install.sh`），预训练模型也可用官方
> 安装脚本一键下载。若未检测到 PyTorch，脚本会提示你先装好本体环境。

### 3. 加入音色

把每个音色的模型与参考音频放进 `GPT-SoVITS-API/voices/` 下的同名子目录（详见 `voices/README.md`）：

```
voices/
  我的音色/
    我的音色-e15.ckpt     # GPT 模型
    我的音色_e8_s184.pth  # SoVITS 模型
    参考音频.wav          # 参考音频
    persona.txt           # 可选：AI 对话人设
```

- 服务启动时会**自动加载扫描到的第一组可用模型**，无需改任何配置；
- 放入新音色后**无需重启**：前台点「刷新模型列表」即可出现；
- 仓库根目录下的 `GPT_weights*/SoVITS_weights*` 目录也会被一并扫描。

### 4. 启动

Windows 双击 `GPT-SoVITS-API/start.bat`（首次运行会自动安装缺失依赖；Python 不在 PATH
时，先运行一次 `install_deps.py` 即可把解释器路径自动写入）；或命令行：

```
cd <你的 GPT-SoVITS 根目录>
python GPT-SoVITS-API/api.py -a 0.0.0.0 -p 9880
```

也可以进入项目目录后启动（会自动定位仓库根目录）：

```
cd GPT-SoVITS-API
python api.py            # 或: python -m tts_api
```

Linux / macOS：

```
bash GPT-SoVITS-API/start.sh   # 可用环境变量 PYTHON_EXE 指定解释器
```

### 5. 使用

- 语音合成台前台：http://127.0.0.1:9880/
- AI 语音对话（测试版）：http://127.0.0.1:9880/chat
- API 文档：http://127.0.0.1:9880/docs
- 健康检查：http://127.0.0.1:9880/health

## 需要自行修改的占位符

| 位置 | 占位内容 | 说明 |
|------|----------|------|
| `start.bat` | `PYTHON_EXE=python` | 你的 Python 解释器路径（如 conda 环境）；运行 `install_deps.py` 后会自动替换 |
| `tts_api/config.py` | `voices/example/...` | 默认参考音频 / 默认模型占位路径（不改也能用：服务自动选用扫描到的模型，前台可选参考音频） |
| `tts_api/config.py` | `EXTRA_MODEL_DIRS` / `REF_SEARCH_DIRS` | 额外模型 / 参考音频目录（按需取消注释） |
| `tts_api/config.py` | `DEFAULT_CHAT_SYSTEM_PROMPT` | AI 对话默认人设（更推荐给音色目录放 `persona.txt`） |

## 常用配置

启动参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `-a` / `--bind_addr` | `0.0.0.0` | 绑定地址 |
| `-p` / `--port` | `9880` | 端口 |
| `--version` | `v2`（或环境变量 `version`） | 模型版本 v1/v2/v3/v4/v2Pro/v2ProPlus，须与训练时一致 |
| `--device` | `auto` | auto / cuda / cpu |
| `--auth-token` | 空 | 非空时 `/audio` 需要 `?token=` |
| `--allowed-ips` | 空 | IP 白名单（逗号分隔），仅限非本地请求的 `/tts`、`/play` |
| `--blocked-ips` | 空 | IP 黑名单 |
| `--cors-origins` | 空 | CORS 允许域名（`*` 表示全部） |
| `--ssl-certfile` / `--ssl-keyfile` | 空 | HTTPS 证书 |
| `--webhook-url` | 空 | 告警 Webhook（限流 / 违禁 / 异常时通知） |
| `--max-text-length` | `500` | 文本长度上限（超出截断） |
| `--require-referer` | 关 | 要求 `/tts`、`/play` 携带 Referer |
| `--switch-auth-token` | 空 | 音色切换鉴权令牌（非空时切换音色接口需 `?token=`，多人共用时可选） |
| `--switch-min-interval` | `0` | 两次成功切换的最小间隔秒数（0=不限，防止反复切换反复加载模型） |

数据目录（首次启动自动创建，均在本项目目录下）：`logs/`、`feedback/`、`temp_audio/`
（合成临时音频保留 1 小时自动清理；`logs`、`feedback` 可分别用环境变量
`TTS_API_LOG_DIR`、`TTS_API_FEEDBACK_DIR` 覆盖）。

## API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 语音合成台前台 |
| POST | `/tts` | 提交合成任务（202 + task_id，轮询取结果；流式模式直接返回音频流） |
| GET | `/task_status/{task_id}` | 任务状态轮询 |
| GET | `/tts` | 查询参数直接合成（curl 友好） |
| GET | `/play` | 浏览器在线试听 |
| GET | `/models` | 音色 / 模型 / 参考音频列表 |
| GET | `/set_voice` | 切换音色（SoVITS+GPT 一次完成，202 异步受理；支持 operator/force/token） |
| GET | `/set_gpt_weights` · `/set_sovits_weights` | 模型热切换（202 异步受理） |
| GET | `/switch_status` | 音色切换状态（当前音色 / 待切换进度） |
| GET | `/health` · `/config` | 状态与配置 |
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
- **切换音色后仍用旧音色**：切换是异步排队的——会等切换请求提交前已排队的任务全部跑完后才生效，
  可轮询 `/switch_status` 或看前台顶部横幅了解进度（前方任务数、预计秒数）。
- **手机浏览器合成成功但无法播放 / 点播放没反应**：本版本已修复（音频直链返回完整 200 响应 +
  前端 fetch+Blob 播放）。如仍异常，可访问 `/debug_audio` 诊断页，按 ①②③④ 逐项测试并反馈日志。

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

## 作者与仓库

- 作者：DeepSeek-V4
- 仓库：[DeepSeek-V4-Pro/GPT-SoVITS-API](https://github.com/DeepSeek-V4-Pro/GPT-SoVITS-API)
- 欢迎 Star、Issue 与 PR，使用中遇到问题请先查看文档与常见问题。

## 许可

[MIT](./LICENSE)。基于 GPT-SoVITS（MIT License）构建。
