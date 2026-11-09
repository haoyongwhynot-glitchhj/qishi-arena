"""极简 Markdown → HTML（标题、表格、列表、代码块、引用、粗体、行内代码、链接）。"""
import html
import re


def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


def render(text):
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1; buf = []
            while j < len(lines) and not lines[j].startswith("```"):
                buf.append(lines[j]); j += 1
            out.append("<pre>" + html.escape("\n".join(buf)) + "</pre>"); i = j + 1; continue
        m = re.match(r"^(#{1,6})\s+(.*)", ln)
        if m:
            n = len(m.group(1)); out.append(f"<h{n + 1}>{inline(m.group(2))}</h{n + 1}>"); i += 1; continue
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows = []; j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")]); j += 1
            t = "<table class='t'><thead><tr>" + "".join(f"<th>{inline(h)}</th>" for h in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(t + "</tbody></table>"); i = j; continue
        if re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            tag = "ol" if re.match(r"^\s*\d+\.", ln) else "ul"
            buf = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                ind = len(lines[i]) - len(lines[i].lstrip())
                item = re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i])
                buf.append(f"<li style='margin-left:{ind * 8}px'>{inline(item)}</li>"); i += 1
            out.append(f"<{tag}>" + "".join(buf) + f"</{tag}>"); continue
        if ln.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(inline(lines[i].lstrip("> "))); i += 1
            out.append("<blockquote>" + "<br>".join(buf) + "</blockquote>"); continue
        if ln.strip():
            buf = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||```|>|\s*([-*]|\d+\.)\s)", lines[i]):
                buf.append(inline(lines[i])); i += 1
            out.append("<p>" + "<br>".join(buf) + "</p>"); continue
        i += 1
    return "\n".join(out)
