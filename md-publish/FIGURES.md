# FIGURES.md — designing figures for md-publish pages

Read this before designing any figure. It is a guide, not a straitjacket: within
this visual language you have full creative freedom — layout, composition,
annotation strategy, and interaction design are yours. If your runtime provides
design skills (e.g. `dataviz` for charts, diagramming guidance), load them and
apply their craft **on top of** the local tokens below; where they conflict on
colors or fonts, this file wins so every figure on the site matches the theme.

## The channel

```
doc.md                        # prose: protected by the fidelity gate, not yours to restyle
figures/                      # your creative zone, one file per figure, next to the md
├── <name>.svg                # static diagrams & charts (preferred)
└── <name>.html               # fragments needing interactivity (JS allowed here only)
```

- Static SVG figure — standard image syntax, **alone on its own line**; alt text
  becomes the caption:
  `![WAM roofline：B·N 与单步耗时的关系](figures/roofline.svg)`
- Interactive fragment — fence with the path; fence body is the caption:
  ````
  ```figure-html figures/speedup-calc.html
  拖动 α 滑块查看期望加速比
  ```
  ````
- Both are inlined at build time (theme fonts cascade in, text is selectable),
  wrapped in `<figure>` + caption, and **exempt from the fidelity gate**. Scripts
  anywhere outside these regions still block the deploy.
- Iterate: edit the figure file → `node scripts/publish.mjs <md> --check` → read
  the `figures` section of the report (bytes, has_script, missing refs). Never
  paste figure code into the document body.

## Design tokens (must-use)

| Token | Value | Use |
| --- | --- | --- |
| ink | `#333` | primary text, axis labels |
| muted | `#777` | secondary labels, captions, gridline labels |
| border | `#dfe2e5` | axes, gridlines, box strokes |
| fill | `#f8f8f8` / `#fafafa` | panel & box backgrounds |
| accent / c1 | `#4183C4` | primary series, measured data, links |
| c2 orange | `#E69F00` | second series |
| c3 green | `#009E73` | accepted / ok / improvement |
| c4 mauve | `#CC79A7` | third series |
| c5 sky | `#56B4E9` | secondary blue; estimates pair with c1 |
| c6 vermillion | `#D55E00` | rejected / error / warning; use sparingly |

Conventions: measured = solid c1, estimated = same hue at ~45% opacity (add a
legend chip pair); the ramp is Okabe-Ito → colorblind-safe, keep its hues.

## Math is ALWAYS KaTeX (site rule, no exceptions)

Every mathematical symbol — Greek letters, `×`, `≈`, `≤`, superscripts, formulas,
variables like $k$ or $\alpha$ — must render through KaTeX. Plain-text math
characters are banned in prose, kit components, and figures alike; the build report's
`math_lint` lists violations.

- **Prose**: normal `$…$` / `$$…$$`. For bulk cleanup of an existing doc run
  `node scripts/mathify.mjs <md>` (dry-run diff; `--apply` to write) and fix the
  remaining lint hits with targeted edits.
- **Kit components in the md body** (stat cards, callouts): just write `$…$` inside
  the raw HTML — the build renders it.
- **HTML figure fragments**: `$…$` anywhere in markup text — rendered at inline time
  (script/style/pre content is left alone). Figure-region math is rendered
  html-only (no MathML layer): Safari mis-positions KaTeX's hidden MathML inside
  foreignObject and paints every formula twice — the build strips it and
  figure-kit.css carries a `display:none` fallback.
- **SVG figures**: `<text>` cannot host KaTeX. Any label containing math goes in a
  `<foreignObject>` island — which is an *authoring* convention only: the build
  transpiles every math-bearing foreignObject into pure SVG (MathJax paths;
  CJK as native `<text>`), positioned from the box geometry. No HTML reaches
  the page, so Safari renders identically to Chromium (Safari cannot lay out
  KaTeX's HTML inside foreignObject correctly). Keep labels single-line and
  simple (one `<div>`, text + `$…$` only) — complex/nested content falls back
  to HTML with a build warning:

  ```xml
  <foreignObject x="480" y="188" width="92" height="32">
    <div xmlns="http://www.w3.org/1999/xhtml"
         style="font-size:13px;color:#333;text-align:center;line-height:30px;">$x'_3$ 重采样</div>
  </foreignObject>
  ```

  Pure-prose labels (「接受」「并行判定」) may stay as `<text>`. `$…$` left in SVG
  `<text>` does not render and triggers a build warning.

## Layout QA (mandatory — figures were shipped broken before this existed)

Rendered text — KaTeX especially — is wider and taller than you guess. Never
trust hand-computed boxes. After EVERY edit:

```bash
node ~/.claude/skills/md-publish/scripts/fig-check.mjs <figure-file> --shots /tmp/shots-<name>
```

- Fix every `overflow` / `overlap` / `outside` until `status: clean`. Each
  overflow reports `needs: WxH` — set the box to at least that plus margin.
- Then **Read the screenshot PNG and look at it**. Clean lint is necessary, not
  sufficient: judge crowding, label-to-point proximity, and balance by eye.
- Size foreignObject boxes generously (≈+30% over your estimate), center the
  text inside; never aim for an exact fit. Render scale varies with the
  reader's viewport, so text width drifts a few percent — `box_warnings` in the
  report are advisory (transparent boxes don't clip); `overlaps`/`outside`/
  `katex_errors` are the hard failures that must reach zero.
- No vertical or rotated text anywhere: the y-axis title goes horizontally at
  the top-left, above the axis.

## Information budget (hard limits)

- At most **8 free-floating text elements** in the plot area (axis ticks and
  axis titles excluded). One idea per figure.
- In-place labels are for data points and inflection points only, and short:
  ≤ 12 CJK chars / ≤ 24 latin chars, one line preferred, two max.
- Provenance, caveats, secondary evidence, derivations, alternate readings —
  ALL of it goes in the caption, never in the plot.
- If two labels fight for space, one of them didn't deserve to be in the plot.

## SVG craft rules

- `viewBox` always; **no fixed `width`/`height` attributes** (CSS scales it);
  target aspect around 16:7 for wide charts, design at ~720–840 units wide.
- **No `font-family` attribute** — inherit the page stack so hanzi and latin
  match the prose. Text: 13px labels, 12px tick/annotation, 15px bold panel
  titles. Fill `#333`/`#777`, never pure black.
- Strokes: 1.5px data lines, 1px axes/grid (`#dfe2e5`), `stroke-linecap="round"`.
  Arrowheads via `<marker>`; dashed (`4 3`) = ideal/limit/budget reference lines.
- Annotate like a paper, not a dashboard: label data points directly on the
  plot instead of forcing a legend lookup; call out regimes/regions with muted
  text; every axis states quantity and unit. But respect the information
  budget above — annotation is for the few numbers the figure exists to show.
- Accessibility: `role="img"` and a one-line `<title>` as first child.
- Density: a figure earns its place by showing structure prose can't — if it's
  three boxes and an arrow, consider whether the prose already says it.

## Interactive fragments (`figures/*.html`)

- Native first: `<details>`, `:hover`, CSS transitions cover most needs (the
  reference roofline page needed zero JS). Reach for JS when state genuinely
  changes (sliders, toggles, recomputed numbers).
- Self-contained only: inline vanilla JS/CSS, **no CDN or external requests**
  (pages must work offline-of-third-parties). Scope DOM queries to your own
  fragment (wrap in a uniquely-classed div); style with the kit classes/vars.
- Fragments live in the page DOM (not iframes): don't define globals, don't
  touch elements outside your wrapper.

## Kit components (raw HTML, usable directly in the md body)

Stat-card row:
```html
<div class="mp-stats">
<div class="mp-stat"><b class="accent">27–46×</b><span>KV 带宽富余</span></div>
<div class="mp-stat"><b>106 GB/s</b><span>实测 HBM 带宽</span></div>
</div>
```

Callout (`mp-callout` = info blue; add `warn` / `fix` / `note`):
```html
<div class="mp-callout warn"><span class="mp-callout-title">诚实注记</span>
本图右侧三根浅色柱为估算值，未经实测。</div>
```

Legend chips: `<div class="mp-legend"><span class="mp-chip"><i></i>实测</span>
<span class="mp-chip estimated"><i></i>估算</span></div>`

Collapsible table: `<details class="mp-table"><summary>▸ 表格视图（40 行）</summary>` +
a normal md/HTML table + `</details>`

## Quality checklist before `--check`

- [ ] viewBox present, no fixed width/height, no font-family attributes
- [ ] Only token colors; measured/estimated convention respected
- [ ] Axes have units; key points annotated in place; zh/en mixed text reads well
- [ ] `<title>` present; caption written (alt text or fence body)
- [ ] Interactive fragment: self-contained, scoped, no external requests
- [ ] Report clean: figure listed, no `missing`, bytes sane (< ~150 KB)
