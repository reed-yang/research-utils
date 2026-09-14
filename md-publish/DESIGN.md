# md-publish — Design Document

Date: 2026-08-12. Status: implemented alongside this doc; open questions resolved by an
evidence-collection workflow (4 GPT-Terra workers) before final implementation.

## Requirements (from user)

1. A skill for coding agents (Claude Code, ideally also Codex CLI) that turns a Markdown
   file into a public web page hosted on Cloudflare.
2. Comfortable typography for LONG mixed Chinese/English documents. Deep reference:
   the user's Typora theme `github.css` (GitHub-flavored, `#write` container, 860px column).
3. Beautiful math rendering (KaTeX-quality).
4. Token-frugal: the agent must not read/rewrite the document content to publish it.
5. Zero fabrication: the published page must contain exactly the Markdown's content —
   guaranteed by architecture, not by prompt discipline.

Requirements 4 and 5 are the same constraint viewed twice: the moment an LLM generates
the HTML it is both spending tokens and rewriting prose. Therefore rendering must be a
deterministic local pipeline; the agent's job is one shell command.

## Environment constraints (measured on this machine, 2026-08-12)

- `pandoc`: MISSING. `cargo`: MISSING → the classic pandoc + pandoc-katex route is out.
- `node` v22.23.1, `npm` 10.9.8, `bun` 1.3.14: present.
- `wrangler` 4.121.0 via npx, already OAuth-authenticated; token has `workers_scripts (write)`.
  Two accounts visible: a work account (visko.ai) and the personal account
  `Siyuan.reed@gmail.com's Account` (`c9dd3a22a6e7f0e162fc8facabc25862`). **Default to the
  personal account**; configurable.
- Skills live in `~/.claude/skills/` and are mirrored to `~/.codex/skills/` (agent-sync).
  Neither is a git repo. House convention: SKILL.md + `scripts/`, scripts print JSON to stdout.

## Approaches considered

**A. pandoc --standalone + filter** — rejected: pandoc not installed, no cargo for the
build-time KaTeX filter; `--katex` alone renders at page load (slow first paint on long
docs, CDN dependency).

**B. Node pipeline: markdown-it + KaTeX server-side + highlight.js (CHOSEN)** — all
rendering at build time; math becomes static HTML spans (page needs only katex.min.css +
fonts, self-hosted); deps pinned in package.json; runs identically under Claude Code and
Codex on any box with node ≥ 18. No runtime JS required on the page.

**C. SSG (VitePress/Quartz on CF)** — rejected for this need: site-shaped, heavier deps,
build container on CF, per-doc publishing is awkward. Can coexist later if a persistent
"digital garden" is wanted.

**Publishing: Cloudflare Workers static assets (CHOSEN)** over Pages (maintenance mode
since ~2025; Workers+assets is the recommended path) and over pagecast (third-party,
per-publish Pages projects, plain rendering). One assets-only worker (default name
`mdpub`) serves every published doc under its own slug:
`https://mdpub.<subdomain>.workers.dev/<slug>/`.

## Architecture

```
~/.claude/skills/md-publish/          # code+assets (synced to ~/.codex/skills/)
├── SKILL.md                          # agent instructions: ONE command
├── DESIGN.md                         # this file
├── package.json / package-lock.json  # pinned: markdown-it, katex plugin, katex, hljs, …
├── scripts/
│   ├── publish.mjs                   # orchestrator: build → verify → deploy → JSON
│   ├── build.mjs                     # md → html (deterministic)
│   └── verify.mjs                    # fidelity check source-md vs rendered-html
└── assets/
    ├── template.html                 # fixed shell; {{title}}/{{lang}}/{{toc}}/{{body}} slots
    └── style.css                     # adapted github.css + CJK layer + KaTeX/hljs imports

~/.local/share/md-publish/            # STATE (shared by Claude & Codex, never synced)
├── config.json                       # account_id, worker name; created on first run
├── manifest.json                     # source path → slug registry (stable URLs on re-publish)
└── site/                             # staging dir = deployed worker assets
    ├── assets/{style.css, katex/…, hljs github.css}
    └── <slug>/index.html
```

Data flow: `publish.mjs <abs.md> [--slug s] [--title t] [--dry-run]` →
build.mjs renders → verify.mjs gates → copy into `site/<slug>/` → `npx wrangler deploy`
(assets-only config generated into the state dir) → print `{status, url, verify:{...}}`.
The agent relays the URL. Token cost is O(1) in document length.

### Rendering decisions

- markdown-it (CommonMark + GFM tables), `typographer: OFF` (no smart-quote rewriting —
  fidelity), `html: true`.
- Math: KaTeX rendered at build time via a markdown-it plugin (exact plugin chosen from
  evidence cards; requirement: `$…$`, `$$…$$` support, server-side renderToString, TeX
  source preserved in MathML `<annotation>` for verification). `throwOnError: false`.
- Code: highlight.js at build time, GitHub theme CSS. No runtime JS.
- Anchors on headings; TOC generated from tokens: fixed left sidebar ≥ 1280px,
  `<details>` block at top otherwise. Footnotes and task lists enabled.
- YAML frontmatter: `title` honored; everything else ignored, never rendered.
- `lang` attribute: `zh-CN` when CJK chars ≥ 5% of text, else `en` (affects font pick
  and line breaking).

### Typography (the github.css adaptation)

Keep the `#write` container id so the Typora theme maps 1:1. Port: column width
(860px / 1024px@1400 / 1200px@1800), headings with h1/h2 bottom borders, table striping,
blockquote, hr, inline-code chips. Drop: Typora app chrome (`#typora-*`, `.md-fences`
→ becomes `pre`, CodeMirror, sidebar, preferences). Replace the Open Sans local-woff2
font stack (files absent) with a system stack + CJK:
`-apple-system, "Segoe UI", "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`.

CJK layer on top:
- `line-height: 1.75` for body (Typora's 1.6 is tight for hanzi), paragraph spacing kept.
- `text-autospace` + `text-spacing-trim` as progressive enhancement (support matrix from
  evidence; unsupported browsers simply ignore). No build-time text mutation by default —
  inserting U+0020 between CJK/Latin (pangu-style) alters the text layer and complicates
  verification; CSS does it losslessly where supported.
- `overflow-x: auto` on `pre` and display-math blocks; `word-break` tuned for CJK.
- Print stylesheet inherited from the theme's `@media print`.
- Light theme only (the reference theme is light-only; dark mode = future work).

### Fidelity gate (verify.mjs)

Rendered HTML is parsed and compared against the source Markdown:
1. Headings: count per level and exact text match, in order.
2. Fenced code blocks: count and byte-identical content.
3. Math: count of KaTeX nodes == count of math segments in source; TeX source extracted
   from MathML `<annotation>` must match the source segment (whitespace-normalized).
4. Prose: non-whitespace character multiset of the Markdown text layer vs the HTML text
   content (after removing code/math), required ≥ 99.9% overlap; hard-fail below.
Deploy is blocked on any mismatch. This turns "no fabrication" from a prompt request
into a checked invariant.

### Publishing details

- Assets-only worker config (JSONC) generated in the state dir; `assets.directory = site/`,
  `not_found_handling: 404`; account pinned via `CLOUDFLARE_ACCOUNT_ID` from config.json.
- Slug policy: default `<sanitized-name>-<6 random hex>` generated once per source path
  and persisted in manifest.json → unguessable-by-default, stable across re-publishes.
  `--slug` overrides (also persisted). No root index page is generated, so one shared
  link does not expose the other documents.
- Wrangler dedupes unchanged assets by hash on deploy; re-deploying the whole site dir
  per publish is the intended flow.
- Deletion: remove `site/<slug>/` + redeploy, or `wrangler delete` for the whole worker.

## Token & fabrication budget (the point of the whole design)

Agent-side work per publish: read SKILL.md (~60 lines, cached), run one command, relay
one URL + verify summary. The document body never enters model context. The model
physically cannot rewrite content because no content flows through it.

## Addendum 2026-08-12: designed-figure channel

User decision after v1 shipped: keep faithful text, but let the model design figures
(diagrams/charts/interactive viz) in the style of their hand-built roofline page.
Chosen mechanism — **sidecar files in `figures/`, inlined at build time** behind
markers, exempt from the fidelity gate; the gate's script rule became "no `<script>`
outside figure regions" (user chose figure JS always-allowed, no flag). Quality comes
from a guide, not hard programmatic limits (user's explicit preference): FIGURES.md
(theme tokens + Okabe-Ito ramp + craft rules) and figure-kit.css components; agents
are encouraged to bring their own design skills on top. Rejected for now: whole-page
raw HTML mode, Observable Plot build-time generator (add when data-driven charts
churn). Known trade-off: figure content is by design NOT fidelity-checked — it is
authored creative work, reviewed by the human via the live URL.

## Risks / open items

- workers.dev subdomain must exist on the account (first deploy may require registering
  one; surfaced by wrangler, documented in SKILL.md).
- KaTeX + CJK inside math (`\text{中文}`) renders with fallback fonts — acceptable.
- Very large embedded images are not handled (md images with remote URLs pass through;
  local images are copied into `site/<slug>/` and rewritten to relative paths).
- Codex CLI skills format compatibility confirmed via evidence worker; skill authored to
  the common subset (name + description frontmatter).
