---
name: export-conversation-to-html
description: Use when the user asks to export, archive, or save the current Claude Code conversation as a shareable HTML file — covers triggers like "导出对话", "导出聊天记录", "保存对话", "对话沉淀", "对话存档", "归档对话", "export conversation", "save chat to html". Reads the most-recently-modified JSONL under ~/.claude/projects/<encoded-cwd>/ (or an explicit path / session UUID) and produces a self-contained ChatGPT-style HTML page: real user input on the right, Claude/tool/skill activity on the left, with a sticky header, per-type filter checkboxes (用户/Claude回复/Skill调用/工具调用/工具结果, default-on for conversation-only view), a tree-style 用户提问导航 TOC with highlighted "回答" tags for AskUserQuestion responses, collapsible tool calls and tool results, Markdown rendering, syntax-highlighted code blocks, and automatic stripping of harness-injected noise (skill content loads, system reminders, hook outputs, image manifests).
---

# Export Conversation to HTML

## Overview

把 Claude Code 一次会话（JSONL 日志）转成单文件 HTML，干净的聊天泡泡风格，方便沉淀为知识资产或在团队中分享。

**核心特性：**
- 🫧 ChatGPT 风格气泡 —— 真实用户输入居右，Claude 回复 / 思考 / 工具调用 / 工具结果居左
- 💬 `AskUserQuestion` 用户选择会被识别为真实用户输入（不是工具结果），单独样式展示 Q→A
- 🧹 自动剥离 harness 注入的合成消息（Skill 加载内容、system-reminder、hook 输出、图片清单）
- 🎛 左侧筛选器 6 类（默认开：用户消息 / 用户回答 / Claude 回复；默认关：Skill 调用 / 工具调用 / 工具结果），点 checkbox 即可切换显隐
- 📑 用户提问导航 —— 树形结构 + 序号 + 回答徽标
- 📌 顶部 header sticky（毛玻璃），滚动时一直在顶
- 📖 Markdown + 代码高亮（marked.js / highlight.js via CDN）

**默认行为：** 导出当前会话（`~/.claude/projects/<encoded-cwd>/` 下最近修改的 `*.jsonl`）到当前目录的 `./conversation-<short-uuid>.html`。

## When to Use

- 用户说 "导出对话" / "保存这次对话" / "把聊天记录存下来" / "export conversation" / "save this chat"
- 用户想归档一次会话用于分享、文档化、知识资产沉淀
- 用户想留下一份易读的工具密集型调试 / 设计 session 记录

Do NOT use for:
- 仅导出单条消息（直接复制即可）
- 实时流式展示（这是一次性静态导出）

## Workflow

1. **Identify the session source.** Default to the current session — the most recently modified `*.jsonl` under `~/.claude/projects/<encoded-cwd>/`, where `<encoded-cwd>` is the cwd with `/` replaced by `-` and a leading `-`. If the user gives a UUID or path, use that instead.

2. **Confirm output location.** Default is `./conversation-<short-uuid>.html`. If the user wants somewhere else (学城附件、桌面、特定目录), use `-o <path>`.

3. **Run the exporter:**
   ```bash
   python3 ~/.claude/skills/export-conversation-to-html/export.py
   # or with options:
   python3 ~/.claude/skills/export-conversation-to-html/export.py -o ~/Desktop/chat.html
   python3 ~/.claude/skills/export-conversation-to-html/export.py --session <uuid>
   python3 ~/.claude/skills/export-conversation-to-html/export.py <path/to/file.jsonl>
   ```

4. **Report the output path** to the user and offer to open it (`open <path>` on macOS).

## What Gets Rendered

| Source block | Rendering | 筛选分类 |
|---|---|---|
| user text | 右侧蓝色气泡 + Markdown | 👤 用户消息 |
| user image attachment | 右侧蓝色气泡（📎 标记） | 👤 用户消息 |
| AskUserQuestion 用户选择 | 右侧蓝色气泡，Q→A 卡片排版 | 💬 用户回答 |
| assistant text | 左侧白色气泡 + Markdown | 🤖 Claude 回复 |
| assistant thinking | 左侧灰色 `<details>` + thinking 徽标 | 💭 思考过程 |
| Skill tool_use | 左侧灰色 `<details>` + ✨ 徽标 | ✨ Skill 调用 |
| Other tool_use | 左侧灰色 `<details>` + 🔧 徽标 + 参数摘要 | 🔧 工具调用 |
| tool_result | 左侧灰色 `<details>` + 📤/❌ 徽标 + 结果摘要 | 📤 工具结果 |
| `<system-reminder>` blocks | Stripped from user text | — |
| Harness-synthetic user messages | Skipped（Skill content load / hook output 等） | — |
| system / attachment / mode events | Skipped（noise） | — |

## Quick Reference

| Flag | Purpose |
|---|---|
| (none) | Export the current session in CWD project |
| `<path.jsonl>` | Explicit JSONL path |
| `--session <uuid>` | Search all projects for a session UUID |
| `--cwd <dir>` | Override project directory inference |
| `-o <out.html>` | Custom output path |

Run `python3 export.py --help` for the full list.

## Output Features

- **Self-contained HTML** — only external deps are CDN-hosted `marked.js` (markdown) and `highlight.js` (code coloring). Works offline if those have been cached.
- **Chat layout** — user bubbles on the right (only真实用户输入), everything else (Claude 回复 / 工具调用 / 工具结果 / 思考) on the left.
- **Sidebar filters** — checkboxes per type (用户 / Claude 回复 / 思考 / Skill 调用 / 工具调用 / 工具结果); default all on. Uncheck to hide that category from the conversation pane. Includes "全选/全不选" toggle.
- **TOC sidebar** — every user turn becomes a clickable anchor.
- **Collapsible internals** — tool calls, results, and thinking blocks default to collapsed; expand on demand.
- **Code highlighting + Markdown** — fenced code blocks, tables, lists, images all render.
- **Noise stripping** — harness-injected synthetic messages (Skill content load, hook output, system reminders, image manifests) are filtered out before rendering, so the right-side bubbles are真正的用户消息.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Running with no JSONL in the project dir | Verify `~/.claude/projects/<encoded-cwd>/` exists; pass explicit path if not |
| Picking up a stale session | The script chooses the most-recently-modified JSONL; pass `--session <uuid>` to be explicit |
| Output overwritten silently | Add `-o` with a unique name when exporting multiple sessions |
| User wanting to share online | Output uses CDN scripts — viewer needs internet; for offline mention inlining marked/highlight |
