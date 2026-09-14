# md-publish

Deterministic Markdown → HTML → Cloudflare pipeline for long Chinese/English documents
with LaTeX math. Built for coding agents (Claude Code / Codex CLI): the agent runs one
command and relays a URL; no document text ever flows through a model, so token cost is
O(1) and fabrication is structurally impossible (and additionally checked).

Agent-facing contract: `SKILL.md`. Design rationale and alternatives: `DESIGN.md`.

## How it works

```
doc.md ──build.mjs──► page html ──verify.mjs──► site/<slug>/ ──wrangler──► public URL
         markdown-it        fidelity gate         staging dir      Workers static assets
         KaTeX (build-time) (blocks deploy        ~/.local/share/  worker "mdpub"
         highlight.js        on any mismatch)      md-publish/site
```

- **`scripts/publish.mjs`** — orchestrator and the only entry point you need.
  `node scripts/publish.mjs <abs.md> [--slug s] [--title t] [--check] | --list | --unpublish <s>`
- **`scripts/build.mjs`** — md → standalone page. markdown-it (CommonMark+GFM,
  `typographer:false` so punctuation is never rewritten), `@mdit/plugin-katex`
  (build-time KaTeX; `$…$`, `$$…$$`, ` ```math ` fences), `@mdit/plugin-tasklist`,
  markdown-it-anchor (CJK-preserving slugs), markdown-it-footnote, highlight.js.
  Emits per-doc TOC (sidebar ≥1600px viewport, `<details>` block otherwise), copies
  local images, detects `lang` from Han-character ratio, and reports stats/warnings
  (including the exact TeX of any formula KaTeX could not fully parse).
- **`scripts/verify.mjs`** — the no-fabrication gate, source md vs rendered page:
  1. heading count + per-heading text (normalized exact match)
  2. every fenced code block byte-identical, in order
  3. every rendered formula's TeX (recovered from MathML `<annotation>`) exists in source
  4. prose: char-frequency overlap ≥98% **and** ordered 12-char-window containment in
     both directions — catches one fabricated or omitted sentence even in a 100k-char
     document, and localizes it in the error message
  5. zero `<script>` tags in the page
  Any failure → exit 2, deploy blocked.

## The theme (how github.css was applied)

`assets/style.css` ports the typography core of the user's Typora `github.css` theme
1:1 — same `#write` column (860px, 1024px ≥1400px, 1200px ≥1800px viewport), heading
scale with h1/h2 bottom borders, table striping, blockquote, hr, inline-code chips —
and maps Typora's `.md-fences` to `pre`. Typora app chrome (`#typora-*`, CodeMirror,
sidebar, preferences) was dropped. On top, a CJK layer:

- font stack: self-hosted Open Sans (latin, as in the theme) → PingFang SC /
  Microsoft YaHei / Noto Sans CJK SC for hanzi; `line-height: 1.75` (theme's 1.6 is
  tight for hanzi)
- `text-autospace: normal` + `text-spacing-trim` behind `@supports` — native,
  lossless inter-script spacing on Chrome/Edge 140+, Safari 18.4+, Firefox 145+;
  no pangu-style space insertion, the text layer stays byte-faithful
- `text-align: justify` for zh pages; `overflow-x: auto` on code and display math
- KaTeX css + fonts and highlight.js github theme, all self-hosted (no CDN)

To restyle: edit `assets/style.css` (per-element) or `assets/template.html`
(page shell). Next publish re-stages assets automatically.

## Designed figures

Model-designed graphics get a scoped, auditable channel that leaves the prose gate
intact: files in `figures/` next to the md are inlined at build time — `.svg` via a
standalone `![caption](figures/x.svg)` image line (other renderers degrade to a plain
image), `.html` fragments via a `figure-html` fence. Figure regions are exempt from
the fidelity checks and are the **only** place `<script>` is allowed (self-contained,
no CDN); a script anywhere else still blocks the deploy. `FIGURES.md` is the design
contract (theme-derived tokens, Okabe-Ito palette, SVG craft rules, quality bar);
`assets/figure-kit.css` ships reusable components (stat cards, callouts, legend chips,
collapsible tables) usable as raw HTML in the md body. The build report's `figures`
section (bytes / has_script / missing) is the iteration loop.

## Hosting model

One assets-only Cloudflare Worker (`mdpub`, free plan: 20k files, 25MiB/file, asset
requests free/unlimited) serves every published doc:

- `https://mdpub.<subdomain>.workers.dev/<slug>/` — slug = `<name>-<6-hex>` by
  default: unguessable, stable across re-publishes (stored in manifest), `noindex`,
  and no root index page — sharing one link does not expose the others.
- State in `~/.local/share/md-publish/`: `config.json` (account_id, worker_name),
  `manifest.json` (source → slug/url), `site/` (exact deployed tree).
- Whole-site redeploy per publish is the intended Workers-assets flow.
- Remove one page: `--unpublish <slug>`. Remove everything:
  `npx wrangler delete --name mdpub` (+ delete the state dir).

## Setup on a new machine

1. `npx wrangler login` (or reuse existing OAuth), `npx wrangler whoami` for the id.
2. Write `~/.local/share/md-publish/config.json`:
   `{"account_id":"<id>","worker_name":"mdpub","compatibility_date":"2026-08-01"}`
   (or export `CLOUDFLARE_ACCOUNT_ID` once — first run persists it).
3. First `publish.mjs` run auto-installs npm deps. Under Codex CLI, `npm install` and
   `wrangler deploy` need network → approve when prompted (default sandbox is offline).

## Troubleshooting

- `deploy failed` + tail mentions subdomain → account has no workers.dev subdomain
  yet: run `npx wrangler deploy --config ~/.local/share/md-publish/wrangler.jsonc`
  once interactively and accept the prompt.
- Two accounts on one OAuth token → the deploy is pinned by `account_id` in
  config.json; change it there.
- Formula shows red on the page → build report's `math.render_errors` quotes the TeX;
  KaTeX supports most but not all LaTeX (see katex.org/docs/supported).
- `--check` staged page can be inspected locally at the `staged` path in the report.
