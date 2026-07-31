# Prompt — Generate a Professional Project Report (self-contained HTML)

Paste this into an AI coding assistant (Claude Code / similar) working **inside
the target project's repo**. It reproduces the report system built for the Wheel
Inspection System: a Markdown source of truth + a stdlib build script that emits
a single **self-contained, offline, shareable HTML** with an SVG flowchart and
embedded screenshots.

---

## The prompt

> You are documenting THIS project as a professional report a manager could read
> and a new engineer could learn from. Produce a maintainable doc set, not a
> throwaway. Work in these steps and show me the plan before writing files.
>
> **1. Investigate first.** Read the entry point, the main modules, the
> dependency manifest (requirements.txt / pyproject / package.json), and any
> config files. Identify: what the project does, its stack + versions, the
> file/module structure and how they call each other, any threading/async
> architecture, the data flow end to end, the key algorithms and the *non-obvious
> decisions* (and WHY each was made), the config knobs and their meaning, how it's
> built/deployed, and its honest limitations. Ask me for anything you can't infer
> (e.g. screenshots, deployment target).
>
> **2. Create `docs/PROJECT_DOCUMENTATION.md`** as the single source of truth,
> in plain language a non-specialist can follow. Include these sections, adapted
> to the project:
>   - One-paragraph description + a "what it does in one picture" ASCII sketch.
>   - **Screenshots** (I will place image files in `docs/`; embed them — see step 4).
>   - A **flowchart** (SVG — see step 5).
>   - Key facts table (the headline numbers/choices).
>   - Software dependencies table (package · version · used for · required?).
>   - File structure tree + a "who calls whom" link diagram.
>   - Architecture (threading/async model) if the project has one.
>   - Data flow, end to end.
>   - Each major subsystem: how it works AND **why this method** (tie every
>     choice to the problem it solved).
>   - Config files: every key and what it controls.
>   - Deployment / how to run.
>   - **Progress table with a Date column**: stage · what · why · how tested.
>   - Glossary of domain terms.
>   Rules: plain words, short sentences, be **honest about limits** (don't
>   oversell accuracy), and label diagrams with the responsible source file.
>
> **3. Create `docs/build_docs.py`** — a **standard-library-only** Markdown→HTML
> converter (no pip installs, must run on the deployment machine). It converts
> the subset the doc uses: headings, bold, inline code, fenced code blocks,
> tables, bullet/numbered lists **(join multi-line/wrapped list items into one
> `<li>`)**, block quotes, horizontal rules, HTML comments (skip them), and
> standalone images `![caption](file)`. Output one self-contained `.html` with
> **all CSS inlined**, light + dark theme via `prefers-color-scheme`, responsive
> (`max-width:100%` images, tables scroll), and a footer noting it's generated.
> No external fonts, scripts, or network references.
>
> **4. Embed images as base64** in build_docs.py so the HTML is a single portable
> file: raster (png/jpg) → `data:` URI; **SVG → inline the markup**; URL-decode
> paths (handle spaces / `%20`); show a visible marker if a file is missing.
>
> **5. Create the flowchart as a hand-written `docs/*.svg`** (vector, embeds
> cleanly, scales). Show the main pipeline(s) as labelled boxes with arrows; put
> the **source file name in grey under each box**; use a decision diamond for
> branches; add a small legend. Keep boxes tall enough that text never overflows.
> If a renderer is available (QtSvg / cairosvg), render it to PNG and visually
> check it's neat before finalizing.
>
> **6. Also create `docs/work_log.md`** (dated, newest first) and
> `docs/CONTEXT_HANDOFF.md` (an engineering brain-dump: non-obvious decisions,
> traps, magic numbers + where they live, known limits, open TODOs, test-data
> situation). These carry knowledge the polished report omits.
>
> **7. Build and verify.** Run `python docs/build_docs.py`. Confirm: all images
> embedded (`data:image` count matches), SVG inline, zero external references,
> no "missing image" markers, no multi-line-list breakage. Report the file size.
>
> **Deliverable:** editing `docs/PROJECT_DOCUMENTATION.md` and re-running
> `build_docs.py` regenerates a single `.html` I can email — it opens offline in
> any browser and can be printed to PDF. Never hand-edit the `.html`.

---

## Notes for the operator

- **Screenshots:** put PNG/JPG files in `docs/` and reference them with
  `![caption](filename)` in the `.md`. Give each a one-line caption.
- **Sharing:** send only the generated `.html` — it carries the images and
  flowchart inside it. The source images/`.md`/`.svg` stay in the repo for
  future rebuilds but don't need to travel.
- **Keeping it current:** the file-structure and architecture sections describe
  the code *at write time*; update them when the code changes (same discipline
  as the work log). Nothing auto-detects drift.
- A working reference implementation of all of the above lives in this repo's
  `docs/` (build_docs.py, PROJECT_DOCUMENTATION.md, pipeline_flowchart.svg).
