---
name: md-publish
description: Use when asked to publish or share a Markdown file as a web page or public URL (发布网页/分享链接/挂到公网), re-publish after edits, list published pages, or take one down — including designing diagrams, charts, or interactive visualizations for those pages (示意图/图表/可视化). Handles long Chinese/English documents with LaTeX math and code.
---

# md-publish

Publish a Markdown file to a public Cloudflare URL with one command. Rendering is a
deterministic local pipeline (markdown-it + build-time KaTeX + highlight.js, Typora
github-theme CSS with a CJK layer) and a fidelity gate blocks deploy if the page text
diverges from the source. The document's content is the pipeline's job, not yours:
publishing never requires generating HTML or reading the md into context.

## The one command

```bash
node ~/.claude/skills/md-publish/scripts/publish.mjs /abs/path/doc.md
```

Prints a JSON report: `url`, `stats` (headings/math/code/tables/images/lang), `verify`,
`warnings`. Relay `url` plus any `warnings` to the user — that is the whole task.

| Variant | Effect |
| --- | --- |
| `--check` | build + verify + stage only, no deploy (fast iteration) |
| `--slug <s>` | choose URL path; default `<name>-<random>` is unguessable and stays stable across re-publishes |
| `--title <t>` | override page title |
| `--list` | all published docs with URLs |
| `--unpublish <slug-or-path>` | take a page down |

## Iterate from the report, not the raw file

- `math failed to render: <tex>` → the failing TeX is quoted verbatim; fix that one
  snippet in the source with a targeted Edit, re-run `--check`.
- `math_lint` non-empty → site rule: ALL math (prose, kit HTML, figures) must be
  KaTeX, never plain-text symbols (α, ×, ≈, x²…). Run
  `node ~/.claude/skills/md-publish/scripts/mathify.mjs <md>` (dry-run; `--apply` to
  write) for the bulk, then fix listed leftovers with targeted edits.
- `images.missing` → listed paths are broken relative links; fix or copy the files.
- `verify` fail → deploy was blocked, nothing went live; the report pinpoints the
  heading index / code fence / TeX / divergent text span with a sample.
- exit codes: 0 ok · 2 verify blocked · 3 deploy failed (`wrangler_tail` shows why).

## Environment facts

- State and config: `~/.local/share/md-publish/config.json` (Cloudflare account_id,
  worker `mdpub`); pages serve at `https://mdpub.<subdomain>.workers.dev/<slug>/`.
- Pages are public but unlisted: random slug, `noindex`, no root index page.
- Dependencies auto-install on first run (needs network); Cloudflare auth comes from
  wrangler OAuth (`npx wrangler whoami` to check).

## Designed figures (示意图 / 图表 / 交互可视化)

Figures are the sanctioned creative zone — when the user wants one, you design it:

1. Read `FIGURES.md` in this skill's directory first (design tokens, craft rules, kit
   components). Load your own design skills (e.g. dataviz) for craft; FIGURES.md wins
   on colors and fonts so every figure matches the site theme.
2. One file per figure in `figures/` next to the md — `.svg` for static diagrams and
   charts, `.html` for interactive fragments (self-contained JS is allowed only there).
3. Reference it from the md: `![caption](figures/name.svg)` alone on its own line, or
   a `figure-html figures/name.html` fence whose body is the caption.
4. QA every figure with `node <skill-dir>/scripts/fig-check.mjs <figure-file> --shots <dir>`
   (headless-Chromium layout lint + screenshot): fix all overflows/overlaps until
   `clean`, then Read the PNG and judge it visually. Iterate the doc with `--check`.
   Reading the relevant section of the doc to design a fitting figure is fine — the
   prohibition is on re-generating the page's prose, which stays gated.

Kit components (`mp-stats` stat cards, `mp-callout` boxes, legend chips, collapsible
tables) work as raw HTML directly in the md — snippets are in FIGURES.md.

## Red flags — stop and run the command instead

- Hand-writing or patching the page HTML "just this once"
- Reading the whole md to "convert" or "reformat" it before publishing
- Pasting document content into the page, the chat, or another tool to publish it
- Inlining SVG/JS into the md body instead of a `figures/` file
