# 音色目录（voices/）

每个子目录 = 一个音色。把训练好的模型与参考音频放进去即可，无需改任何代码：

```
voices/
  我的音色/
    我的音色-e15.ckpt     # GPT 模型（多个时默认取 epoch 最高的）
    我的音色_e8_s184.pth  # SoVITS 模型（多个时默认取最高的）
    参考音频.wav          # 参考音频（可多条；文件名含中文/日文时自动作为参考文本）
    persona.toml          # 可选：AI 语音对话的默认人设（推荐）
  其他音色/
    ...
```

- 服务启动时会**自动加载扫描到的第一组可用模型**，无需修改配置；
- 放入新音色后**无需重启**：前台点「刷新模型列表」即可出现；
- 仓库根目录下的 `GPT_weights*/SoVITS_weights*` 目录也会被一并扫描，可作为补充；
- 想扫描其他目录，可在 `tts_api/config.py` 的 `EXTRA_MODEL_DIRS` / `REF_SEARCH_DIRS` 中添加（占位符示例见注释）。

## 音色人设（persona，供 AI 语音对话使用）

每个音色目录下可放一个人设文件，系统提示词留空时自动加载并跟随音色切换。
按优先级查找 **`persona.toml` → `persona.json` → 旧版 `persona.txt`**（纯文本仍兼容，直接作为完整提示词）。

### persona.toml（推荐，结构化分区）

```toml
[personality]       # 身份设定
name = "小盐"       # 名字（必填时生成「你的名字是…」开头）
title = "面包师傅"  # 称呼 / 头衔（可选）
identity = """      # 身份设定（你是谁）
...
"""
background = """    # 背景经历
...
"""
character = """     # 性格特征
...
"""
relationship = """  # 人物关系与称呼
...
"""

[behavior]          # 行为准则
behavior_style = """   # 如何接话 / 回应
...
"""
reply_style = """      # 说话风格
...
"""

[chat]              # 聊天注意事项
rules = """         # 该聊天中的注意事项
...
"""

[output]            # 输出格式要求
requirements = """  # 回复会交给语音合成朗读时的约束
...
"""
```

渲染规则：`name` + `title` 生成开头行；其余字段按「1. 身份设定 / 2. 背景经历 / … / 8. 输出格式要求」
八个分区渲染为带分隔线的系统提示词；**空字段对应的分区整体省略**。每个分区支持多行文本。

> 注意：TOML 多行字符串用三引号 `"""`；仅 Python 3.11+ 内置 `tomllib`，
> 更老的 Python 会自动回退 `tomli` / `tomlkit`（`install_deps.py` 会装好）。

### persona.json（等价写法）

```json
{
  "personality": { "name": "小盐", "title": "面包师傅", "identity": "…", "background": "…" },
  "behavior":    { "behavior_style": "…", "reply_style": "…" },
  "chat":        { "rules": "…" },
  "output":      { "requirements": "…" }
}
```

### persona.txt（旧版兼容）

文件内容整体作为系统提示词原文，不做分区渲染。

> 注意：本目录下的音色文件属于你的私有素材，请勿提交到公开仓库（已在 .gitignore 中忽略）。
