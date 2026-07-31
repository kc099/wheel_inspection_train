"""Regenerate PROJECT_DOCUMENTATION.html from PROJECT_DOCUMENTATION.md.

The .md file is the single source of truth. Edit it, then run:

    python docs/build_docs.py

This writes a self-contained, offline-friendly .html (all CSS inlined, no
external fonts or scripts) next to the .md. Uses only the Python standard
library, so it runs on the production machine without extra installs.

Supports the Markdown subset the documentation uses: headings, bold, inline
code, fenced code blocks (for the flowcharts), tables, bullet/numbered lists,
horizontal rules, block quotes, HTML comments, and paragraphs.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent
SRC = DOCS / "PROJECT_DOCUMENTATION.md"
OUT = DOCS / "PROJECT_DOCUMENTATION.html"

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1rem;
  font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.6; color: #1b1b1f; background: #f6f7f9;
}
main { max-width: 960px; margin: 0 auto; background: #fff;
  padding: 2.5rem 3rem; border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08); }
h1 { font-size: 2rem; color: #0d3b66; border-bottom: 3px solid #0d3b66;
  padding-bottom: .3rem; margin-top: 0; }
h2 { font-size: 1.5rem; color: #0d3b66; margin-top: 2.2rem;
  border-bottom: 1px solid #d5dbe2; padding-bottom: .2rem; }
h3 { font-size: 1.2rem; color: #1b5e20; margin-top: 1.6rem; }
h4 { font-size: 1.05rem; color: #333; }
p { margin: .7rem 0; }
code { background: #eef1f4; padding: .12em .38em; border-radius: 4px;
  font-family: Consolas, Menlo, monospace; font-size: .9em; }
pre { background: #1e2430; color: #e6edf3; padding: 1rem 1.2rem;
  border-radius: 8px; overflow-x: auto; line-height: 1.4; }
pre code { background: none; padding: 0; color: inherit; font-size: .86rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0;
  font-size: .93rem; }
th, td { border: 1px solid #d5dbe2; padding: .5rem .7rem; text-align: left;
  vertical-align: top; }
th { background: #eaf2fb; color: #0d3b66; }
tr:nth-child(even) td { background: #f8fafc; }
blockquote { border-left: 4px solid #f0a202; background: #fff8e6;
  margin: 1rem 0; padding: .6rem 1rem; color: #5b4a1a; }
hr { border: none; border-top: 1px solid #d5dbe2; margin: 2rem 0; }
ul, ol { margin: .6rem 0 .6rem 1.4rem; }
li { margin: .25rem 0; }
a { color: #0d6efd; }
.docmeta { color: #6b7280; font-size: .85rem; margin-top: 2.5rem;
  border-top: 1px solid #d5dbe2; padding-top: .8rem; }
figure { margin: 1.4rem 0; text-align: center; }
figure img { max-width: 100%; height: auto; border: 1px solid #d5dbe2;
  border-radius: 8px; }
figure.flow svg { max-width: 100%; height: auto; }
figcaption { color: #5b6b7a; font-size: .9rem; margin-top: .5rem;
  font-style: italic; }
@media (prefers-color-scheme: dark) {
  figure img { border-color: #30363d; }
  figure.flow { background: #f6f7f9; border-radius: 8px; padding: .5rem; }
}
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #c9d1d9; }
  main { background: #161b22; box-shadow: none; }
  h1, h2 { color: #58a6ff; border-color: #30363d; }
  h3 { color: #7ee787; }
  h4 { color: #c9d1d9; }
  code { background: #21262d; }
  th { background: #1f2937; color: #58a6ff; }
  th, td { border-color: #30363d; }
  tr:nth-child(even) td { background: #1a212b; }
  blockquote { background: #24200f; color: #e3c766; }
  hr { border-color: #30363d; }
}
"""


def _inline(text: str) -> str:
    """Apply inline markdown to an already HTML-escaped string."""
    # inline code first, protecting its contents from bold/link parsing
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\x00(\d+)\x00",
                  lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)
    return text


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _embed_image(caption: str, rel_path: str) -> str:
    """Inline an image so the HTML is a single portable file.

    SVG is inlined as markup (crisp + scalable); raster images (png/jpg) are
    embedded as base64 data URIs. Missing files degrade to a caption note.
    """
    import base64
    from urllib.parse import unquote

    path = (DOCS / unquote(rel_path)).resolve()
    cap = f"<figcaption>{_inline(html.escape(caption))}</figcaption>" if caption else ""
    if not path.exists():
        return (f'<figure><p style="color:#b00">[missing image: '
                f'{html.escape(rel_path)}]</p>{cap}</figure>')
    if path.suffix.lower() == ".svg":
        svg = path.read_text(encoding="utf-8")
        svg = re.sub(r"<\?xml.*?\?>", "", svg, flags=re.DOTALL).strip()
        return f'<figure class="flow">{svg}{cap}</figure>'
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp"}.get(path.suffix.lower().lstrip("."),
                                                          "application/octet-stream")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return (f'<figure><img alt="{html.escape(caption)}" '
            f'src="data:{mime};base64,{b64}">{cap}</figure>')


def convert(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # HTML comment block (the "edit the .md" banner) — skip it.
        if line.lstrip().startswith("<!--"):
            while i < n and "-->" not in lines[i]:
                i += 1
            i += 1
            continue

        # standalone image / figure:  ![caption](file)
        img = re.match(r"^!\[(.*?)\]\((.+?)\)\s*$", line.strip())
        if img:
            out.append(_embed_image(img.group(1), img.group(2)))
            i += 1
            continue

        # fenced code block (flowcharts)
        if line.startswith("```"):
            i += 1
            block: list[str] = []
            while i < n and not lines[i].startswith("```"):
                block.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(block) + "</code></pre>")
            continue

        # table (header row, separator row, then body)
        if line.strip().startswith("|") and i + 1 < n and re.match(
                r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            header = _cells(line)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{_inline(html.escape(c))}</th>" for c in header)
                       + "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join(
                    f"<td>{_inline(html.escape(c))}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue

        # heading
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(html.escape(m.group(2)))}</h{level}>")
            i += 1
            continue

        # horizontal rule
        if re.match(r"^---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # block quote (possibly multi-line)
        if line.lstrip().startswith(">"):
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(html.escape(lines[i].lstrip()[1:].strip()))
                i += 1
            out.append("<blockquote>" + _inline(" ".join(quote)) + "</blockquote>")
            continue

        # unordered / ordered list
        if re.match(r"^\s*[-*]\s+", line) or re.match(r"^\s*\d+\.\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>")
            while i < n and (re.match(r"^\s*[-*]\s+", lines[i])
                             or re.match(r"^\s*\d+\.\s+", lines[i])):
                item = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", lines[i])
                i += 1
                # Join wrapped continuation lines (indented, non-blank, and not
                # the start of a new list item or block) into this same item —
                # otherwise multi-line bullets spill out as loose paragraphs.
                while (i < n and lines[i].strip()
                       and not re.match(r"^\s*(?:[-*]|\d+\.)\s+", lines[i])
                       and not re.match(r"^(#{1,6}\s|```|---+\s*$|>|\|)", lines[i])):
                    item += " " + lines[i].strip()
                    i += 1
                out.append(f"<li>{_inline(html.escape(item))}</li>")
            out.append(f"</{tag}>")
            continue

        # blank line
        if not line.strip():
            i += 1
            continue

        # paragraph (gather until blank / block start)
        para = []
        while i < n and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|```|---+\s*$|\s*[-*]\s|\s*\d+\.\s|>|\|)", lines[i]):
            para.append(html.escape(lines[i].strip()))
            i += 1
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")

    return "\n".join(out)


def main() -> None:
    md = SRC.read_text(encoding="utf-8")
    body = convert(md)
    page = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Wheel Inspection System — Documentation</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n{body}\n"
        "<p class=\"docmeta\">Generated from PROJECT_DOCUMENTATION.md by "
        "build_docs.py. Do not edit this HTML by hand.</p>\n"
        "</main>\n</body>\n</html>\n"
    )
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT}  ({len(page):,} bytes)")


if __name__ == "__main__":
    main()
