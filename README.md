# export-conversation-to-html

把 Claude Code 一次会话（JSONL 日志）转成单文件 HTML —— 干净的 ChatGPT 风格聊天泡泡，方便沉淀为知识资产或在团队中分享。

> 真实用户输入靠右，Claude 回复 / 思考 / 工具调用 / 工具结果靠左；harness 注入的合成消息自动剥离，只留下你真正想留下的对话。

## 特性

- 🫧 **ChatGPT 风格气泡** —— 右侧真实用户输入，左侧 Claude 回复 / 思考 / 工具调用 / 工具结果
- 💬 **`AskUserQuestion` 用户选择** —— 单独识别为「用户回答」，Q→A 卡片排版，不会和工具结果混在一起
- 🧹 **自动剥离 harness 噪音** —— Skill 加载内容、`<system-reminder>`、hook 输出、图片清单等合成消息会被过滤
- 🎛 **侧边筛选器** —— 6 类 checkbox（👤 用户消息 / 💬 用户回答 / 🤖 Claude 回复 / 💭 思考 / ✨ Skill 调用 / 🔧 工具调用 / 📤 工具结果），默认开「对话三件套」，工具相关默认收起
- 📑 **用户提问导航 TOC** —— 树形结构 + 序号 + 「回答」徽标，长会话快速跳转
- 📌 **顶部 sticky header（毛玻璃）** —— 滚动时始终可见
- 📖 **Markdown + 代码高亮** —— marked.js + highlight.js（CDN）
- 📦 **单文件 HTML** —— 一个文件就能分享

## 安装

作为 Claude Code Skill 使用（推荐）：

```bash
mkdir -p ~/.claude/skills/export-conversation-to-html
cp SKILL.md export.py ~/.claude/skills/export-conversation-to-html/
```

之后在 Claude Code 里说「导出对话」「保存这次聊天」「export this conversation」等，Skill 会自动激活。

也可以直接当独立脚本跑：

```bash
python3 export.py
```

> 仅依赖 Python 3 标准库；无需 `pip install`。

## 使用

```bash
# 默认：导出当前 CWD 项目下最近的会话到 ./conversation-<short-uuid>.html
python3 export.py

# 指定输出位置
python3 export.py -o ~/Desktop/chat.html

# 通过 session UUID 在所有项目里搜索
python3 export.py --session 4e065235-20a5-46e4-80af-3056ea4f2186

# 显式指定 JSONL 文件
python3 export.py ~/.claude/projects/-Users-me-myproj/abc123.jsonl

# 切换项目目录（覆盖 CWD 推断）
python3 export.py --cwd /path/to/some/project
```

| Flag | 用途 |
|---|---|
| (无参) | 当前 CWD 项目最近的 session |
| `<path.jsonl>` | 显式 JSONL 路径 |
| `--session <uuid>` | 跨所有项目搜索 session |
| `--cwd <dir>` | 覆盖项目目录推断 |
| `-o <out.html>` | 自定义输出路径 |

## 它会渲染什么

| 来源 | 渲染方式 | 筛选分类 |
|---|---|---|
| 用户文本 | 右侧蓝色气泡 + Markdown | 👤 用户消息 |
| 用户图片附件 | 右侧蓝色气泡（📎 标记） | 👤 用户消息 |
| `AskUserQuestion` 用户选择 | 右侧蓝色气泡，Q→A 卡片 | 💬 用户回答 |
| Assistant 文本 | 左侧白色气泡 + Markdown | 🤖 Claude 回复 |
| Assistant thinking | 左侧灰色 `<details>` + 徽标 | 💭 思考过程 |
| Skill `tool_use` | 左侧灰色 `<details>` + ✨ | ✨ Skill 调用 |
| 其他 `tool_use` | 左侧灰色 `<details>` + 🔧 + 参数摘要 | 🔧 工具调用 |
| `tool_result` | 左侧灰色 `<details>` + 📤/❌ + 结果摘要 | 📤 工具结果 |
| `<system-reminder>` | 从用户文本中剥离 | — |
| harness 合成用户消息 | 跳过（Skill 加载 / hook 输出 / 图片清单等） | — |
| system / attachment / mode 事件 | 跳过（noise） | — |

## Session 路径约定

Claude Code 把每个 CWD 当成独立 project，会话日志位于：

```
~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl
```

`<encoded-cwd>` = 当前工作目录的绝对路径，把 `/` 全部替换为 `-`，并保留前导的 `-`。例如：

```
/Users/yulixing/myproj  →  -Users-yulixing-myproj
```

不带参数运行时，脚本会找到当前 CWD 对应目录下**最近修改的** `*.jsonl`。

## 常见问题

| 现象 | 解决 |
|---|---|
| 报「找不到 JSONL」 | 确认 `~/.claude/projects/<encoded-cwd>/` 存在；或用显式路径 |
| 导出的是上次的 session | 脚本拿最近修改的；用 `--session <uuid>` 锁定 |
| 多次导出文件互相覆盖 | 加 `-o` 自定义文件名 |
| 在线分享后图片/代码渲染异常 | 输出用了 CDN（marked / highlight.js）；离线场景需要把这两个 inline |

## 项目结构

```
.
├── SKILL.md     # Claude Code Skill 元数据与使用说明（供 Skill 加载器读取）
├── export.py    # 实际的转换脚本（Python 3，无三方依赖）
└── README.md    # 你正在看的这个
```

## License

MIT
