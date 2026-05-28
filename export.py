#!/usr/bin/env python3
"""Export a Claude Code session JSONL file to a self-contained chat-style HTML.

Usage:
  export.py                       # auto-detect current session in CWD project
  export.py <jsonl_path>          # explicit JSONL path
  export.py --session <uuid>      # session by uuid (searches all projects)
  export.py -o <output.html>      # custom output path

Output defaults to ./conversation-<short-uuid>.html in the current directory.
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def encode_project_dir(cwd: str) -> str:
    # Claude Code encodes the project directory by replacing '/' with '-'
    return "-" + cwd.lstrip("/").replace("/", "-")


def find_current_session_jsonl(cwd: str) -> Path:
    project_dir = PROJECTS_ROOT / encode_project_dir(cwd)
    if not project_dir.is_dir():
        raise SystemExit(f"Project dir not found: {project_dir}")
    jsonl_files = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not jsonl_files:
        raise SystemExit(f"No JSONL session files in {project_dir}")
    return jsonl_files[0]


def find_session_by_uuid(uuid: str) -> Path:
    for project_dir in PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{uuid}.jsonl"
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Session {uuid} not found under {PROJECTS_ROOT}")


def safe_load_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print(f"warn: skip malformed line {i}", file=sys.stderr)


def short(text: str, n: int = 80) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n] + "..."


def strip_system_reminders(text: str) -> str:
    # Drop <system-reminder>...</system-reminder> blocks from user text
    return re.sub(
        r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL
    ).strip()


def fmt_value(v) -> str:
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, indent=2)
    except Exception:
        return str(v)


def render_tool_input(inp) -> str:
    if not isinstance(inp, dict):
        return f"<pre><code>{html.escape(fmt_value(inp))}</code></pre>"
    parts = []
    for k, v in inp.items():
        val = fmt_value(v)
        if "\n" in val or len(val) > 100:
            parts.append(
                f'<div class="kv"><span class="k">{html.escape(k)}</span>'
                f'<pre><code>{html.escape(val)}</code></pre></div>'
            )
        else:
            parts.append(
                f'<div class="kv"><span class="k">{html.escape(k)}</span>'
                f'<span class="v">{html.escape(val)}</span></div>'
            )
    return "\n".join(parts)


def render_tool_result_content(content) -> str:
    if isinstance(content, list):
        chunks = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    chunks.append(c.get("text", ""))
                elif c.get("type") == "image":
                    chunks.append("[image]")
                else:
                    chunks.append(fmt_value(c))
            else:
                chunks.append(str(c))
        return "\n".join(chunks)
    if isinstance(content, str):
        return content
    return fmt_value(content)


def is_synthetic_user_text(rec, uuid_map) -> bool:
    """Detect harness-injected user messages.

    Covers all known injection paths:
    - Skill content loaded after `Skill` tool call (parent is a `user` with `tool_result`)
    - Skill content loaded via slash command (parent is a `user` with string content)
    - Hook outputs / system reminders chained off any user record

    Rule: any `user` record whose parent is another `user` record is synthetic.
    Real user input always has a non-user parent (`attachment`, `system`,
    `last-prompt`, etc.) because the harness inserts boundary markers between
    real turns.
    """
    parent_uuid = rec.get("parentUuid")
    if not parent_uuid:
        return False
    parent = uuid_map.get(parent_uuid)
    if not parent:
        return False
    return parent.get("type") == "user"


SLASH_NAME_RE = re.compile(r"<command-name>([^<]+)</command-name>")
SLASH_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def clean_slash_command(text: str) -> str:
    """Normalize slash-command wrapped user input.

    Input: <command-message>X</command-message> <command-name>/X</command-name> <command-args>ARGS</command-args>
    Output: `/X`\\n\\nARGS  (markdown-ish so the command name shows as inline code)
    """
    if "<command-name>" not in text:
        return text
    name_m = SLASH_NAME_RE.search(text)
    args_m = SLASH_ARGS_RE.search(text)
    name = (name_m.group(1).strip() if name_m else "")
    args = (args_m.group(1).strip() if args_m else "")
    if name and args:
        return f"`{name}`\n\n{args}"
    if name:
        return f"`{name}`"
    return text


def collect_messages(records):
    """Walk the raw JSONL records and emit normalized chat items."""
    uuid_map = {r.get("uuid"): r for r in records if "uuid" in r}

    # Build tool_use_id -> tool_name map (used to special-case AskUserQuestion).
    tool_id_to_name = {}
    for r in records:
        if r.get("type") == "assistant":
            for b in r.get("message", {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_id_to_name[b.get("id")] = b.get("name", "")

    items = []
    for rec in records:
        t = rec.get("type")

        if t == "user":
            msg = rec.get("message", {})
            content = msg.get("content", "")
            ts = rec.get("timestamp", "")
            if isinstance(content, str):
                text = strip_system_reminders(content)
                text = clean_slash_command(text)
                if text:
                    items.append({"kind": "user_text", "text": text, "ts": ts})
            elif isinstance(content, list):
                synthetic = is_synthetic_user_text(rec, uuid_map)
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_result":
                        tool_id = block.get("tool_use_id")
                        tool_name = tool_id_to_name.get(tool_id, "")
                        # AskUserQuestion: render as a real user message (the user
                        # actually clicked these answers).
                        if tool_name == "AskUserQuestion":
                            answers = (rec.get("toolUseResult") or {}).get("answers") or {}
                            if answers:
                                items.append({
                                    "kind": "user_qa",
                                    "answers": answers,
                                    "ts": ts,
                                })
                                continue
                        items.append({
                            "kind": "tool_result",
                            "tool_use_id": tool_id,
                            "tool_name": tool_name,
                            "content": render_tool_result_content(block.get("content", "")),
                            "is_error": block.get("is_error", False),
                            "ts": ts,
                        })
                    elif btype == "text":
                        # Skip harness-injected text (skill content, etc.)
                        if synthetic:
                            continue
                        text = strip_system_reminders(block.get("text", ""))
                        # Skip "[Image: source: ...]" manifest descriptors
                        if text.startswith("[Image: source:"):
                            continue
                        if text:
                            items.append({"kind": "user_text", "text": text, "ts": ts})
                    elif btype == "image":
                        # Real user image attachment
                        if synthetic:
                            continue
                        items.append({"kind": "user_image", "ts": ts})

        elif t == "assistant":
            msg = rec.get("message", {})
            content = msg.get("content", [])
            ts = rec.get("timestamp", "")
            model = msg.get("model", "")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        items.append({
                            "kind": "assistant_text",
                            "text": text,
                            "ts": ts,
                            "model": model,
                        })
                elif btype == "thinking":
                    text = block.get("thinking", "").strip()
                    if text:
                        items.append({
                            "kind": "thinking",
                            "text": text,
                            "ts": ts,
                        })
                elif btype == "tool_use":
                    items.append({
                        "kind": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                        "ts": ts,
                    })

        # Skip: system, attachment (hook outputs), mode, permission-mode, last-prompt, ai-title, file-history-snapshot
    return items


def category_for(item) -> str:
    k = item["kind"]
    if k in ("user_text", "user_image"):
        return "user"
    if k == "user_qa":
        return "user_qa"
    if k == "assistant_text":
        return "assistant"
    if k == "thinking":
        return "thinking"
    if k == "tool_use":
        return "skill" if item.get("name") == "Skill" else "tool_use"
    if k == "tool_result":
        return "tool_result"
    return "other"


# Filter UI: category, label, default-on
CATEGORIES = [
    ("user", "👤 用户消息", True),
    ("user_qa", "💬 用户回答", True),
    ("assistant", "🤖 Claude 回复", True),
    ("thinking", "💭 思考过程", False),
    ("skill", "✨ Skill 调用", False),
    ("tool_use", "🔧 工具调用", False),
    ("tool_result", "📤 工具结果", False),
]


def render_html(items, session_id: str, source_path: Path) -> str:
    # Build TOC entries from user messages (one per turn).
    toc_entries = []
    for idx, it in enumerate(items):
        if it["kind"] == "user_text":
            toc_entries.append({
                "id": f"msg-{idx}",
                "title": short(it["text"], 60),
            })
        elif it["kind"] == "user_qa":
            answers = it.get("answers") or {}
            preview = " / ".join(answers.values())
            toc_entries.append({
                "id": f"msg-{idx}",
                "title": short(preview, 56),
                "is_qa": True,
            })

    # Count per category for filter labels.
    cat_counts = {c[0]: 0 for c in CATEGORIES}
    for it in items:
        cat = category_for(it)
        if cat in cat_counts:
            cat_counts[cat] += 1
    default_on = {c[0] for c in CATEGORIES if c[2]}

    pieces = []
    for idx, it in enumerate(items):
        anchor_id = f"msg-{idx}"
        cat = category_for(it)
        ts_label = ""
        if it.get("ts"):
            try:
                ts_label = datetime.fromisoformat(it["ts"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_label = it["ts"]

        if it["kind"] == "user_text":
            pieces.append(f'''<div id="{anchor_id}" data-cat="{cat}" class="row row-user">
  <div class="bubble bubble-user">
    <div class="meta">用户 · {ts_label}</div>
    <div class="md">{html.escape(it["text"])}</div>
  </div>
  <div class="avatar avatar-user">U</div>
</div>''')
        elif it["kind"] == "assistant_text":
            model_label = it.get("model", "")
            pieces.append(f'''<div id="{anchor_id}" data-cat="{cat}" class="row row-asst">
  <div class="avatar avatar-asst">C</div>
  <div class="bubble bubble-asst">
    <div class="meta">Claude{(" · " + model_label) if model_label else ""}{(" · " + ts_label) if ts_label else ""}</div>
    <div class="md">{html.escape(it["text"])}</div>
  </div>
</div>''')
        elif it["kind"] == "thinking":
            pieces.append(f'''<div id="{anchor_id}" data-cat="{cat}" class="row row-asst">
  <div class="avatar avatar-asst">C</div>
  <div class="bubble bubble-asst tool">
    <details>
      <summary><span class="badge badge-think">thinking</span> <span class="tool-summary">{html.escape(short(it["text"], 100))}</span></summary>
      <div class="md">{html.escape(it["text"])}</div>
    </details>
  </div>
</div>''')
        elif it["kind"] == "tool_use":
            name = it.get("name", "")
            summary = ""
            inp = it.get("input", {})
            if isinstance(inp, dict):
                for key in ("command", "description", "file_path", "skill", "prompt", "pattern", "query"):
                    if key in inp:
                        summary = short(fmt_value(inp[key]), 100)
                        break
            badge_label = "✨ " + name if name == "Skill" else "🔧 " + name
            pieces.append(f'''<div id="{anchor_id}" data-cat="{cat}" class="row row-asst">
  <div class="avatar avatar-asst">C</div>
  <div class="bubble bubble-asst tool">
    <details>
      <summary><span class="badge badge-tool">{html.escape(badge_label)}</span> <span class="tool-summary">{html.escape(summary)}</span></summary>
      {render_tool_input(inp)}
    </details>
  </div>
</div>''')
        elif it["kind"] == "tool_result":
            content_text = it.get("content", "")
            badge_cls = "badge-err" if it.get("is_error") else "badge-result"
            badge_text = "❌ tool_result" if it.get("is_error") else "📤 tool_result"
            pieces.append(f'''<div id="{anchor_id}" data-cat="{cat}" class="row row-asst">
  <div class="avatar avatar-tool">⚙</div>
  <div class="bubble bubble-tool">
    <details>
      <summary><span class="badge {badge_cls}">{badge_text}</span> <span class="tool-summary">{html.escape(short(content_text, 100))}</span></summary>
      <pre><code>{html.escape(content_text)}</code></pre>
    </details>
  </div>
</div>''')
        elif it["kind"] == "user_image":
            pieces.append(f'''<div id="{anchor_id}" data-cat="{cat}" class="row row-user">
  <div class="bubble bubble-user">
    <div class="meta">用户 · {ts_label}</div>
    <div class="md">📎 [图片附件]</div>
  </div>
  <div class="avatar avatar-user">U</div>
</div>''')
        elif it["kind"] == "user_qa":
            qa_html = "\n".join(
                f'<div class="qa-pair">'
                f'<div class="qa-q">Q: {html.escape(q)}</div>'
                f'<div class="qa-a">A: {html.escape(a)}</div>'
                f'</div>'
                for q, a in it["answers"].items()
            )
            pieces.append(f'''<div id="{anchor_id}" data-cat="{cat}" class="row row-user">
  <div class="bubble bubble-user qa">
    <div class="meta">用户回答（AskUserQuestion）· {ts_label}</div>
    {qa_html}
  </div>
  <div class="avatar avatar-user">U</div>
</div>''')

    toc_html = "\n".join(
        f'<li><a href="#{e["id"]}">'
        f'<span class="toc-idx">{i+1:02d}</span>'
        f'{"<span class=\"toc-qa-tag\">回答</span>" if e.get("is_qa") else ""}'
        f'{html.escape(e["title"])}'
        f'</a></li>'
        for i, e in enumerate(toc_entries)
    )

    filter_html = "\n".join(
        f'<label class="filter-item"><input type="checkbox" data-filter="{cat}"'
        f'{" checked" if on else ""}>'
        f'<span class="filter-label">{html.escape(label)}</span>'
        f'<span class="filter-count">{cat_counts[cat]}</span></label>'
        for cat, label, on in CATEGORIES if cat_counts[cat] > 0
    )

    body_html = "\n".join(pieces)
    title = f"Conversation · {session_id[:8]}"

    return TEMPLATE.replace("{{TITLE}}", html.escape(title)) \
        .replace("{{SESSION_ID}}", html.escape(session_id)) \
        .replace("{{SOURCE}}", html.escape(str(source_path))) \
        .replace("{{EXPORTED_AT}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")) \
        .replace("{{TOC}}", toc_html) \
        .replace("{{FILTERS}}", filter_html) \
        .replace("{{BODY}}", body_html) \
        .replace("{{COUNT}}", str(len(items)))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{{TITLE}}</title>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github.min.css">
<script src="https://cdn.jsdelivr.net/npm/highlight.js@11/lib/common.min.js"></script>
<style>
  :root {
    --anchor-offset: 110px;
    --bg: #f7f7f8;
    --panel: #ffffff;
    --border: #e5e5e7;
    --text: #1f2328;
    --muted: #6b7280;
    --user-bg: #d3e8ff;
    --asst-bg: #ffffff;
    --tool-bg: #f4f4f6;
    --accent: #5b8def;
    --code-bg: #f6f8fa;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  .layout { display: flex; min-height: 100vh; }
  .sidebar {
    width: 280px;
    background: var(--panel);
    border-right: 1px solid var(--border);
    padding: 16px;
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
    flex-shrink: 0;
  }
  .sidebar h2 {
    margin: 0 0 6px;
    font-size: 14px;
    color: var(--muted);
    font-weight: 600;
  }
  .sidebar .session-info {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 16px;
    word-break: break-all;
  }
  .toc {
    list-style: none; padding: 0; margin: 0;
    border-left: 2px solid var(--border);
    margin-left: 6px;
  }
  .toc li {
    margin: 0;
    position: relative;
    padding-left: 14px;
  }
  .toc li::before {
    content: "";
    position: absolute;
    left: -2px; top: 12px;
    width: 10px; height: 2px;
    background: var(--border);
  }
  .toc a {
    display: block;
    padding: 6px 8px;
    border-radius: 6px;
    color: var(--text);
    text-decoration: none;
    font-size: 13px;
    line-height: 1.4;
  }
  .toc a .toc-idx {
    color: var(--accent);
    font-weight: 600;
    margin-right: 6px;
    font-variant-numeric: tabular-nums;
  }
  .toc a:hover { background: var(--bg); }
  .toc a:hover .toc-idx { text-decoration: underline; }
  .toc-qa-tag {
    display: inline-block;
    background: linear-gradient(135deg, #5b8def, #6b46c1);
    color: #fff;
    font-size: 10.5px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    margin-right: 6px;
    letter-spacing: 0.5px;
    vertical-align: middle;
    box-shadow: 0 1px 2px rgba(91,141,239,0.3);
  }
  .sidebar-section { margin-bottom: 18px; }
  .sidebar-section h2 { display: flex; justify-content: space-between; align-items: center; }
  .filter-actions {
    font-size: 11px; color: var(--accent); cursor: pointer;
    font-weight: 500; user-select: none;
  }
  .filter-actions:hover { text-decoration: underline; }
  .filter-list { display: flex; flex-direction: column; gap: 2px; }
  .filter-item {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 8px; border-radius: 6px; cursor: pointer;
    font-size: 13px; color: var(--text);
    transition: background .12s;
  }
  .filter-item:hover { background: var(--bg); }
  .filter-item input { margin: 0; cursor: pointer; flex-shrink: 0; }
  .filter-label { flex: 1; }
  .filter-count {
    background: var(--bg); color: var(--muted);
    padding: 1px 8px; border-radius: 10px; font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .row.hidden { display: none; }
  /* Offset anchor scroll targets so the sticky header doesn't cover them. */
  .row[id],
  .md :is(h1,h2,h3,h4)[id],
  .md :is(strong,b)[id] {
    scroll-margin-top: var(--anchor-offset);
  }
  .main { flex: 1; max-width: 900px; margin: 0 auto; padding: 0 32px 80px; min-width: 0; }
  .stage-panel {
    width: 240px; flex-shrink: 0;
    padding: 16px 12px;
    border-left: 1px solid var(--border);
    background: var(--panel);
    position: sticky; top: 0;
    align-self: flex-start;
    max-height: 100vh;
    overflow-y: auto;
    font-size: 13px;
  }
  .stage-panel h3 {
    margin: 0 0 10px; font-size: 13px;
    color: var(--muted); font-weight: 600;
  }
  .stage-list { list-style: none; padding: 0; margin: 0; }
  .stage-list li { margin: 2px 0; }
  .stage-list a {
    display: block; padding: 5px 8px; border-radius: 5px;
    color: var(--text); text-decoration: none; line-height: 1.4;
    border-left: 2px solid transparent;
  }
  .stage-list a:hover {
    background: var(--bg); border-left-color: var(--accent); color: var(--accent);
  }
  .stage-l1 { padding-left: 0; font-weight: 600; }
  .stage-l2 { padding-left: 8px; }
  .stage-l3 { padding-left: 18px; }
  .stage-l4 { padding-left: 28px; }
  .stage-list a.active {
    background: var(--bg); border-left-color: var(--accent); color: var(--accent);
  }
  .md a[target="_blank"]::after {
    content: " ↗"; font-size: 0.85em; color: var(--muted);
  }
  @media (max-width: 1100px) {
    .stage-panel { display: none !important; }
  }
  .mobile-menu-btn { display: none; }
  .sidebar-backdrop { display: none; }
  @media (max-width: 768px) {
    .layout { display: block; }
    .sidebar {
      position: fixed; top: 0; left: 0; bottom: 0;
      width: 84%; max-width: 320px;
      z-index: 100;
      transform: translateX(-100%);
      transition: transform .22s ease;
      box-shadow: 2px 0 14px rgba(0,0,0,0.18);
      height: 100vh;
    }
    .sidebar.open { transform: translateX(0); }
    .sidebar-backdrop {
      display: block;
      position: fixed; inset: 0;
      background: rgba(0,0,0,0.4);
      z-index: 99;
      opacity: 0;
      pointer-events: none;
      transition: opacity .22s ease;
    }
    .sidebar-backdrop.visible {
      opacity: 1;
      pointer-events: auto;
    }
    .mobile-menu-btn {
      display: flex;
      position: fixed;
      bottom: 20px; left: 20px;
      z-index: 50;
      width: 46px; height: 46px; border-radius: 50%;
      background: var(--accent); color: #fff;
      border: none;
      box-shadow: 0 4px 14px rgba(91,141,239,0.45);
      align-items: center; justify-content: center;
      font-size: 22px; cursor: pointer;
    }
    .mobile-menu-btn:active { transform: scale(0.94); }
    .main {
      padding: 0 14px 60px;
      max-width: 100%;
    }
    header.page {
      padding: 14px 0 10px;
      margin-bottom: 14px;
    }
    header.page h1 { font-size: 17px; }
    header.page .sub {
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      direction: rtl;
      text-align: left;
    }
    .row { gap: 8px; margin: 14px 0; }
    .avatar { width: 28px; height: 28px; font-size: 12px; }
    .bubble { max-width: 86%; padding: 9px 12px; border-radius: 10px; }
    .md { font-size: 13.5px; }
    .meta { font-size: 10.5px; }
    .badge { font-size: 10px; padding: 2px 6px; }
    .tool-summary { font-size: 11px; }
    .bubble.qa .qa-pair { padding: 6px 8px; }
    .bubble.qa .qa-q { font-size: 11.5px; }
    .bubble.qa .qa-a { font-size: 13px; }
  }
  header.page {
    position: sticky;
    top: 0;
    z-index: 10;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    padding: 18px 0 14px;
    margin-bottom: 20px;
    backdrop-filter: saturate(180%) blur(6px);
    -webkit-backdrop-filter: saturate(180%) blur(6px);
  }
  header.page h1 { margin: 0 0 4px; font-size: 22px; }
  header.page .sub {
    color: var(--muted); font-size: 13px;
    word-break: break-all;
  }
  .row { display: flex; gap: 12px; margin: 18px 0; align-items: flex-start; }
  .row-user { flex-direction: row-reverse; }
  .avatar {
    width: 34px; height: 34px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-weight: 600; font-size: 14px; color: #fff; flex-shrink: 0;
  }
  .avatar-user { background: #5b8def; }
  .avatar-asst { background: #6b46c1; }
  .avatar-tool { background: #9ca3af; }
  .bubble {
    max-width: 78%;
    background: var(--asst-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px 14px;
    word-wrap: break-word;
  }
  .bubble-user { background: var(--user-bg); border-color: #b8d4ff; }
  .bubble.tool { background: var(--tool-bg); }
  .bubble-tool { background: var(--tool-bg); border-color: #d1d5db; }
  .bubble.qa .qa-pair {
    margin: 6px 0;
    padding: 8px 10px;
    background: rgba(255,255,255,0.6);
    border-radius: 8px;
    border-left: 3px solid var(--accent);
  }
  .bubble.qa .qa-q {
    font-size: 12.5px; color: var(--muted);
    margin-bottom: 4px; font-weight: 500;
  }
  .bubble.qa .qa-a {
    font-size: 14px; color: var(--text); font-weight: 500;
  }
  .meta { font-size: 11px; color: var(--muted); margin-bottom: 6px; }
  .md { font-size: 14px; }
  .md p { margin: 6px 0; }
  .md h1, .md h2, .md h3 { margin: 12px 0 6px; }
  .md ul, .md ol { padding-left: 22px; }
  .md table { border-collapse: collapse; margin: 8px 0; }
  .md th, .md td { border: 1px solid var(--border); padding: 4px 8px; font-size: 13px; }
  .md code {
    background: var(--code-bg); padding: 1px 5px; border-radius: 4px;
    font-family: "SF Mono", Monaco, Consolas, monospace; font-size: 12.5px;
  }
  .md pre {
    background: var(--code-bg); border-radius: 8px;
    padding: 12px; overflow-x: auto; margin: 8px 0;
  }
  .md pre code { background: transparent; padding: 0; font-size: 12.5px; }
  .md img { max-width: 100%; border-radius: 6px; margin: 6px 0; }
  .md blockquote {
    border-left: 3px solid var(--accent);
    padding-left: 10px; color: var(--muted); margin: 6px 0;
  }
  details { margin: 0; }
  summary {
    cursor: pointer; list-style: none;
    display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
    user-select: none;
  }
  summary::-webkit-details-marker { display: none; }
  summary::before {
    content: "▸"; color: var(--muted);
    transition: transform .15s; display: inline-block;
  }
  details[open] > summary::before { transform: rotate(90deg); }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600;
  }
  .badge-tool { background: #e0e7ff; color: #4338ca; }
  .badge-result { background: #d1fae5; color: #065f46; }
  .badge-err { background: #fee2e2; color: #991b1b; }
  .badge-think { background: #fef3c7; color: #92400e; }
  .tool-summary {
    color: var(--muted); font-size: 12px;
    font-family: "SF Mono", Monaco, monospace;
  }
  .kv { margin: 6px 0; font-size: 12.5px; }
  .kv .k {
    display: inline-block; font-weight: 600;
    color: var(--accent); margin-right: 8px;
  }
  .kv .v { font-family: "SF Mono", Monaco, monospace; }
  .kv pre { margin: 4px 0; }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="session-info">
      Session: {{SESSION_ID}}<br>
      Exported: {{EXPORTED_AT}}<br>
      Items: {{COUNT}}
    </div>
    <div class="sidebar-section">
      <h2>🎛 筛选 <span class="filter-actions" id="filter-toggle-all">全选/全不选</span></h2>
      <div class="filter-list">
        {{FILTERS}}
      </div>
    </div>
    <div class="sidebar-section">
      <h2>📑 用户提问导航</h2>
      <ul class="toc">
        {{TOC}}
      </ul>
    </div>
  </aside>
  <main class="main">
    <header class="page">
      <h1>{{TITLE}}</h1>
      <div class="sub">来源：{{SOURCE}}</div>
    </header>
    <div id="conversation">
      {{BODY}}
    </div>
  </main>
  <aside class="stage-panel" id="stage-panel" hidden></aside>
</div>
<div class="sidebar-backdrop" id="sidebar-backdrop"></div>
<button class="mobile-menu-btn" id="mobile-menu-btn" aria-label="打开侧栏">≡</button>
<script>
  // Render markdown inside .md blocks.
  marked.setOptions({ breaks: true, gfm: true });
  document.querySelectorAll('.md').forEach(el => {
    const raw = el.textContent;
    el.innerHTML = marked.parse(raw);
  });
  // Code highlighting.
  document.querySelectorAll('pre code').forEach(el => {
    try { hljs.highlightElement(el); } catch (e) {}
  });
  // External links → open in new tab with an ↗ marker.
  document.querySelectorAll('.md a[href]').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (/^https?:\/\//i.test(href)) {
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
    }
  });
  // Extract stage / phase markers from assistant content. Picks up both
  // markdown headers (## 阶段 ...) and bold paragraphs (**阶段 ...**), since
  // Claude often emits the latter. Hierarchy depth is derived from the dotted
  // stage number itself (1 → L1, 1.1 → L2, 1.1.2 → L3 ...), not the HTML tag.
  const stageRe = /^(阶段|Stage|Phase|步骤|Step)\s*([\d.]+)/i;
  const stages = [];
  const seenForDup = new WeakSet();
  let stageIdx = 0;
  const candidates = document.querySelectorAll(
    '.row[data-cat="assistant"] .md :is(h1,h2,h3,h4,strong,b)'
  );
  candidates.forEach(el => {
    if (seenForDup.has(el)) return;
    const isInline = (el.tagName === 'STRONG' || el.tagName === 'B');
    if (isInline) {
      // Only treat a bold span as a stage marker if it leads a paragraph
      // (i.e. it's the first element child of its <p>).
      const parent = el.parentElement;
      if (!parent || parent.tagName !== 'P') return;
      if (parent.firstElementChild !== el) return;
    }
    const txt = (el.textContent || '').trim();
    const m = stageRe.exec(txt);
    if (!m) return;
    // Prevent double-counting: when a header contains a <strong>, only keep
    // the outer header.
    if (!isInline) {
      el.querySelectorAll('strong, b').forEach(c => seenForDup.add(c));
    }
    const dots = ((m[2] || '').match(/\./g) || []).length;
    const level = Math.min(dots + 1, 4);
    const id = 'stage-' + (++stageIdx);
    el.id = id;
    stages.push({ id, title: txt, level });
  });
  // Mobile sidebar toggle (off-canvas drawer).
  const sidebarEl = document.querySelector('.sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  const menuBtn = document.getElementById('mobile-menu-btn');
  function closeSidebar() {
    sidebarEl?.classList.remove('open');
    backdrop?.classList.remove('visible');
  }
  function openSidebar() {
    sidebarEl?.classList.add('open');
    backdrop?.classList.add('visible');
  }
  menuBtn?.addEventListener('click', () => {
    if (sidebarEl?.classList.contains('open')) closeSidebar(); else openSidebar();
  });
  backdrop?.addEventListener('click', closeSidebar);
  // Close drawer after tapping any link inside it (filters keep it open).
  sidebarEl?.querySelectorAll('a').forEach(a => a.addEventListener('click', closeSidebar));
  // Dynamic scroll offset: measure sticky header height + buffer, write to
  // CSS var so anchor jumps (TOC + 阶段导航) clear the floating header.
  const stickyHeader = document.querySelector('header.page');
  if (stickyHeader) {
    const setAnchorOffset = () => {
      const h = stickyHeader.offsetHeight || 90;
      document.documentElement.style.setProperty('--anchor-offset', (h + 80) + 'px');
    };
    setAnchorOffset();
    window.addEventListener('resize', setAnchorOffset);
  }
  const stagePanel = document.getElementById('stage-panel');
  if (stages.length > 0 && stagePanel) {
    stagePanel.innerHTML =
      '<h3>🎯 阶段导航</h3>' +
      '<ul class="stage-list" id="stage-list">' +
      stages.map(s => `<li><a class="stage-l${s.level}" href="#${s.id}">${s.title}</a></li>`).join('') +
      '</ul>';
    stagePanel.hidden = false;
    // Highlight current section using IntersectionObserver.
    const stageList = document.getElementById('stage-list');
    const links = new Map();
    stages.forEach(s => links.set(s.id, stageList.querySelector(`a[href="#${s.id}"]`)));
    const io = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting) {
          links.forEach(a => a.classList.remove('active'));
          const a = links.get(e.target.id);
          if (a) a.classList.add('active');
        }
      });
    }, { rootMargin: '-80px 0px -70% 0px' });
    stages.forEach(s => {
      const el = document.getElementById(s.id);
      if (el) io.observe(el);
    });
  }
  // Type filters.
  const checkboxes = document.querySelectorAll('.filter-item input[type=checkbox]');
  const rows = document.querySelectorAll('.row[data-cat]');
  function applyFilters() {
    const enabled = new Set();
    checkboxes.forEach(cb => { if (cb.checked) enabled.add(cb.dataset.filter); });
    rows.forEach(r => {
      r.classList.toggle('hidden', !enabled.has(r.dataset.cat));
    });
  }
  checkboxes.forEach(cb => cb.addEventListener('change', applyFilters));
  document.getElementById('filter-toggle-all').addEventListener('click', () => {
    const anyOff = Array.from(checkboxes).some(cb => !cb.checked);
    checkboxes.forEach(cb => { cb.checked = anyOff; });
    applyFilters();
  });
  // Apply initial filter state on load (some categories default-off).
  applyFilters();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsonl", nargs="?", help="Path to JSONL session file")
    ap.add_argument("--session", help="Session UUID (search all projects)")
    ap.add_argument("--cwd", default=os.getcwd(), help="Project directory (defaults to current dir)")
    ap.add_argument("-o", "--output", help="Output HTML path")
    args = ap.parse_args()

    if args.jsonl:
        source = Path(args.jsonl).expanduser().resolve()
    elif args.session:
        source = find_session_by_uuid(args.session)
    else:
        source = find_current_session_jsonl(args.cwd)

    if not source.is_file():
        raise SystemExit(f"Not a file: {source}")

    records = list(safe_load_jsonl(source))
    items = collect_messages(records)
    session_id = source.stem
    html_out = render_html(items, session_id, source)

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
    else:
        out_path = Path.cwd() / f"conversation-{session_id[:8]}.html"

    out_path.write_text(html_out, encoding="utf-8")
    print(f"Exported {len(items)} items → {out_path}")
    print(f"Source: {source}")


if __name__ == "__main__":
    main()
