#!/usr/bin/env node
/**
 * build.mjs — deterministic Markdown -> standalone HTML page.
 *
 * No LLM in the loop: markdown-it (CommonMark+GFM) + KaTeX rendered at build
 * time + highlight.js. Emits a compact JSON report so a coding agent can
 * iterate on the document from stats/warnings alone, without reading raw text.
 *
 * CLI: node build.mjs <input.md> [--out <dir>] [--slug <s>] [--title <t>]
 * Module: buildDoc(mdPath, {title}) -> { page, title, lang, srcSha, stats, warnings, images }
 */

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

import MarkdownIt from 'markdown-it';
import anchor from 'markdown-it-anchor';
import footnote from 'markdown-it-footnote';
import { katex } from '@mdit/plugin-katex';
import { tasklist } from '@mdit/plugin-tasklist';
import hljs from 'highlight.js';
import katexLib from 'katex';
import { texToPlacedSvg, mixedTextToTex } from './tex2svg.mjs';

const SKILL_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TEMPLATE = fs.readFileSync(path.join(SKILL_DIR, 'assets', 'template.html'), 'utf8');

/* KaTeX errorColor; scanned in output to surface broken formulas in the report */
const KATEX_ERROR_COLOR = '#cc0000';
/* Math regions are wrapped in these markers so verify.mjs can excise them */
export const MATH_OPEN = '<!--mp:m-->';
export const MATH_CLOSE = '<!--/mp:m-->';
/* Designed-figure regions (inlined SVG / HTML fragments): excised from the
   fidelity gate; the only place where <script> is permitted */
export const FIG_OPEN = '<!--mp:fig-->';
export const FIG_CLOSE = '<!--/mp:fig-->';

export function stripFrontmatter(raw) {
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\s*\r?\n/);
  if (!m) return { body: raw, fmTitle: null };
  const tm = m[1].match(/^title:\s*["']?(.+?)["']?\s*$/m);
  return { body: raw.slice(m[0].length), fmTitle: tm ? tm[1] : null };
}

export function slugifyHeading(s) {
  const base = String(s).trim().toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^\p{L}\p{N}\-_]/gu, '');
  return base || 'section';
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function decodeEntities(s) {
  return s
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, '&');
}

/* Real HTML tags only: "<" must be followed by a letter or "/". A bare "<" used
   as a comparison in prose ("B·N < 206 … > 3") must NOT be treated as a tag —
   the naive <[^>]+> once swallowed 470 chars of a real document between two
   comparison operators. */
export const HTML_TAG_RE = /<\/?[a-zA-Z][\w-]*(?:\s[^<>]*)?\/?>/g;

/* Remove every OPEN…CLOSE region (markers never nest) */
export function exciseMarked(s, OPEN, CLOSE) {
  return s.split(OPEN).map((seg, i) => (i === 0 ? seg : seg.slice(seg.indexOf(CLOSE) + CLOSE.length))).join(' ');
}

export function extractAnnotations(html) {
  const out = [];
  const re = /<annotation encoding="application\/x-tex">([\s\S]*?)<\/annotation>/g;
  let m;
  while ((m = re.exec(html))) out.push(decodeEntities(m[1]));
  return out;
}

/* Site rule: ALL math is KaTeX — including inside raw-HTML kit components and
   figure files. This renders $…$ / $$…$$ found in an HTML fragment (script,
   style and pre content is left untouched). */
export function renderMathInHtmlFragment(html, warnings, ctxLabel, katexOpts = {}) {
  const guards = [];
  let s = html.replace(/<(script|style|pre)\b[\s\S]*?<\/\1>/gi, (m) => {
    guards.push(m);
    return `\uE000G${guards.length - 1}\uE000`;
  });
  const render = (tex, displayMode) => {
    try {
      // fragments are XML/HTML: "<" in math arrives as &lt; — decode before KaTeX
      return MATH_OPEN + katexLib.renderToString(decodeEntities(tex), {
        displayMode, output: 'htmlAndMathml', throwOnError: false,
        errorColor: KATEX_ERROR_COLOR, strict: 'ignore', ...katexOpts,
      }) + MATH_CLOSE;
    } catch (e) {
      warnings.push(`math render failed in ${ctxLabel}: ${e.message}`);
      return tex;
    }
  };
  s = s.replace(/\$\$([^$]+)\$\$/g, (_, tex) => render(tex, true));
  // $…$ must hug its content (pandoc-style) so "$5 and $10" is not math
  s = s.replace(/\$(\S(?:[^$\n]*\S)?)\$/g, (_, tex) => render(tex, false));
  return s.replace(/\uE000G(\d+)\uE000/g, (_, i) => guards[+i]);
}

/* Plain-text math symbols that should have been $…$-wrapped (site rule) */
const PLAIN_MATH_RE = /[α-ωΑ-Ω×÷±≤≥≈≠∞√∑∏∫∈∀∃−⁰¹²³⁴-⁹₀-₉]|\^\{|\\frac/;

export function lintPlainMath(text, label) {
  const hits = [];
  const lines = text.split(/\r?\n/);
  let inFence = false;
  let inMath = false; // inside a multi-line $$ … $$ block
  for (let i = 0; i < lines.length; i++) {
    if (/^ {0,3}(`{3,}|~{3,})/.test(lines[i])) { inFence = !inFence; continue; }
    if (inFence) continue;
    const noPairs = lines[i].replace(/\$\$[^$]*\$\$/g, ' ');
    if (inMath) { if (noPairs.includes('$$')) inMath = false; continue; }
    if ((noPairs.match(/\$\$/g) || []).length % 2 === 1) { inMath = true; continue; }
    // ignore whatever is already inside math or inline code
    const stripped = noPairs
      .replace(/\$[^$\n]+\$/g, ' ')
      .replace(/`[^`]*`/g, ' ');
    const m = stripped.match(PLAIN_MATH_RE);
    if (m) hits.push({ where: `${label}:${i + 1}`, symbol: m[0], snippet: stripped.trim().slice(0, 80) });
  }
  return hits;
}

/* Math-bearing foreignObject labels in SVG figures are transpiled to pure SVG
   (MathJax paths + native <text> for CJK), positioned from the box geometry.
   Rationale: Safari cannot lay out KaTeX's HTML inside foreignObject
   consistently; pure SVG renders identically in every engine. Authoring
   convention is unchanged — foreignObject never reaches the page. */
export function transformSvgFigureMath(inner, warnings, ref) {
  return inner.replace(/<foreignObject\b([^>]*)>([\s\S]*?)<\/foreignObject>/g, (whole, foAttrs, foContent) => {
    if (!/\$/.test(foContent)) return whole; // mathless HTML is Safari-safe as-is
    const dm = foContent.match(/^\s*<div\b([^>]*)>([\s\S]*?)<\/div>\s*$/);
    const complex = !dm || /</.test(dm[2]);
    const num = (name) => {
      const m = foAttrs.match(new RegExp(`\\b${name}="([\\d.-]+)"`));
      return m ? parseFloat(m[1]) : null;
    };
    const box = { x: num('x'), y: num('y'), w: num('width'), h: num('height') };
    if (complex || box.x === null || box.y === null || !box.w || !box.h) {
      warnings.push(`figure ${ref}: complex/unsized foreignObject with math kept as HTML — may render inconsistently in Safari`);
      return renderMathInHtmlFragment(whole, warnings, ref, { output: 'html' });
    }
    const style = dm[1];
    const get = (prop, fb) => {
      const m = style.match(new RegExp(`${prop}\\s*:\\s*([^;"']+)`));
      return m ? m[1].trim() : fb;
    };
    const tex = mixedTextToTex(decodeEntities(dm[2]).trim(), {
      bold: /font-weight\s*:\s*(bold|[6-9]00)/.test(style),
    });
    try {
      const align = get('text-align', 'center');
      const placed = texToPlacedSvg(tex, box, {
        fontSize: parseFloat(get('font-size', '13')) || 13,
        color: get('color', '#333333'),
        align: align === 'left' || align === 'right' ? align : 'center',
      });
      if (placed.width > box.w + 2) {
        warnings.push(`figure ${ref}: label wider than its box (${Math.round(placed.width)}px > ${box.w}px): ${tex.slice(0, 60)}`);
      }
      return placed.svg;
    } catch (e) {
      warnings.push(`figure ${ref}: MathJax failed (${String(e.message).slice(0, 80)}) — kept as HTML`);
      return renderMathInHtmlFragment(whole, warnings, ref, { output: 'html' });
    }
  });
}

function readFigureFile(ref, figCtx) {
  const abs = path.resolve(figCtx.mdDir, ref);
  if (!fs.existsSync(abs)) return null;
  let inner = fs.readFileSync(abs, 'utf8');
  if (/\.svg$/i.test(ref)) {
    // inline just the <svg> element (drop XML prolog / doctype / leading comments)
    const at = inner.indexOf('<svg');
    if (at > 0) inner = inner.slice(at);
    if (!/viewBox=/.test(inner)) figCtx.warnings.push(`figure ${ref}: svg has no viewBox (won't scale responsively)`);
    // math labels become pure SVG (MathJax) so Safari renders like Chromium
    inner = transformSvgFigureMath(inner, figCtx.warnings, ref);
    const outside = inner.replace(/<foreignObject\b[\s\S]*?<\/foreignObject>/g, ' ');
    if (/\$\S(?:[^$\n]*\S)?\$/.test(outside)) {
      figCtx.warnings.push(`figure ${ref}: $…$ outside <foreignObject> cannot render — move that label into a foreignObject`);
    }
    figCtx.lint.push(...lintPlainMath(outside.replace(HTML_TAG_RE, ' '), ref));
  } else {
    inner = renderMathInHtmlFragment(inner, figCtx.warnings, ref, { output: 'html' });
    figCtx.lint.push(...lintPlainMath(
      inner.replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, ' ').replace(HTML_TAG_RE, ' '), ref));
  }
  figCtx.figures.push({
    src: ref,
    mode: /\.svg$/i.test(ref) ? 'inline-svg' : 'inline-html',
    bytes: Buffer.byteLength(inner),
    has_script: /<script\b/i.test(inner),
  });
  return inner;
}

function figureHtml(inner, caption, figCtx) {
  // captions obey the all-math-is-KaTeX rule too: escape first, then render $…$
  // (html-only output like all figure-region math — gate-exempt, Safari-safe)
  const cap = caption
    ? renderMathInHtmlFragment(escapeHtml(caption), figCtx.warnings, 'figure caption', { output: 'html' })
    : '';
  return FIG_OPEN + '<figure class="mp-figure">' + inner
    + (cap ? `<figcaption>${cap}</figcaption>` : '')
    + '</figure>' + FIG_CLOSE;
}

function missingFigureHtml(ref, figCtx) {
  figCtx.warnings.push(`figure file not found: ${ref}`);
  figCtx.figures.push({ src: ref, mode: 'missing', bytes: 0, has_script: false });
  return FIG_OPEN + `<figure class="mp-figure mp-figure-missing"><figcaption>missing figure: ${escapeHtml(ref)}</figcaption></figure>` + FIG_CLOSE;
}

/* A paragraph that is exactly one image pointing into figures/*.svg becomes an
   inlined <figure> (theme fonts cascade in, text selectable, CSS applies).
   Other renderers (GitHub/Typora) still see a plain image — no new syntax. */
export function isFigureRef(src) {
  return /^(?:\.\/)?figures\/[^)]+\.svg$/i.test(src || '');
}

function transformFigures(tokens, figCtx) {
  for (let i = 0; i + 2 < tokens.length; i++) {
    if (tokens[i].type !== 'paragraph_open' || tokens[i + 1].type !== 'inline'
      || tokens[i + 2].type !== 'paragraph_close') continue;
    const kids = (tokens[i + 1].children || []).filter((t) => !(t.type === 'text' && !t.content.trim()));
    if (kids.length !== 1 || kids[0].type !== 'image' || !isFigureRef(kids[0].attrGet('src'))) continue;
    const ref = kids[0].attrGet('src');
    const caption = kids[0].content; // the alt text
    const inner = readFigureFile(ref, figCtx);
    kids[0].type = 'html_inline';
    kids[0].children = null;
    kids[0].content = inner === null ? missingFigureHtml(ref, figCtx) : figureHtml(inner, caption, figCtx);
    tokens[i].hidden = true;
    tokens[i + 2].hidden = true;
    tokens[i + 1].children = kids;
  }
}

function makeMd(warnings, figCtx) {
  const md = new MarkdownIt({
    html: true,
    linkify: true,
    typographer: false, // fidelity: never rewrite quotes/dashes
    highlight: (str, lang) => {
      if (lang && hljs.getLanguage(lang)) {
        try {
          return `<pre><code class="hljs language-${lang}">`
            + hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
            + '</code></pre>';
        } catch (e) {
          warnings.push(`highlight failed for lang=${lang}: ${e.message}`);
        }
      }
      return ''; // fall back to markdown-it's escaped block
    },
  });
  md.use(anchor, { slugify: slugifyHeading, tabIndex: false });
  md.use(footnote);
  md.use(tasklist);
  md.use(katex, {
    delimiters: 'all',
    mathFence: true,
    output: 'htmlAndMathml', // keeps original TeX in MathML <annotation> for verification
    throwOnError: false,
    errorColor: KATEX_ERROR_COLOR,
    strict: 'ignore',
  });
  // Wrap math output in comment markers so verify.mjs can excise math regions
  for (const rule of Object.keys(md.renderer.rules)) {
    if (!/^math_/.test(rule)) continue;
    const orig = md.renderer.rules[rule];
    md.renderer.rules[rule] = (...a) => MATH_OPEN + orig(...a) + MATH_CLOSE;
  }
  // Raw-HTML blocks in the md (kit components) also obey the all-math-is-KaTeX
  // rule: $…$ inside them is rendered at build time
  md.renderer.rules.html_block = (tokens, idx) =>
    renderMathInHtmlFragment(tokens[idx].content, warnings, 'html block');
  // ```math fences render through the fence rule, not math_block — wrap those.
  // ```figure-html <path> fences inline an HTML/SVG fragment as a designed figure
  // (fence body = caption); scripts are permitted inside figure regions only.
  const origFence = md.renderer.rules.fence;
  md.renderer.rules.fence = (tokens, idx, ...rest) => {
    const [kind, ref] = (tokens[idx].info || '').trim().split(/\s+/);
    if (kind === 'figure-html' || kind === 'figure') {
      if (!ref) { warnings.push('figure fence without a file path'); return ''; }
      const inner = readFigureFile(ref, figCtx);
      const caption = tokens[idx].content.trim();
      return (inner === null ? missingFigureHtml(ref, figCtx) : figureHtml(inner, caption, figCtx)) + '\n';
    }
    const rendered = origFence(tokens, idx, ...rest);
    return kind === 'math' ? MATH_OPEN + rendered + MATH_CLOSE : rendered;
  };
  return md;
}

function inlineText(inlineTok) {
  if (!inlineTok || !inlineTok.children) return inlineTok ? inlineTok.content : '';
  let s = '';
  for (const t of inlineTok.children) {
    if (t.type === 'text' || t.type === 'code_inline' || t.type === 'math_inline') s += t.content;
    else if (t.children) s += inlineText(t);
  }
  return s.trim();
}

/* TOC entry markup: site rule says even TOC math is KaTeX-rendered */
function inlineTocHtml(inlineTok, warnings) {
  if (!inlineTok || !inlineTok.children) return escapeHtml(inlineTok ? inlineTok.content : '');
  let s = '';
  for (const t of inlineTok.children) {
    if (t.type === 'math_inline') {
      try {
        s += katexLib.renderToString(t.content, { output: 'html', throwOnError: false, strict: 'ignore' });
      } catch (e) { s += escapeHtml(t.content); warnings.push(`toc math failed: ${e.message}`); }
    } else if (t.type === 'text' || t.type === 'code_inline') s += escapeHtml(t.content);
    else if (t.children) s += inlineTocHtml(t, warnings);
  }
  return s.trim();
}

function buildToc(headings) {
  if (!headings.length) return '';
  const multiH1 = headings.filter((h) => h.level === 1).length > 1;
  const [minL, maxL] = multiH1 ? [1, 3] : [2, 4];
  const items = headings.filter((h) => h.level >= minL && h.level <= maxL);
  if (items.length < 3) return '';
  let res = '';
  let level = minL - 1;
  for (const h of items) {
    while (level < h.level) { res += '<ul>'; level++; }
    while (level > h.level) { res += '</ul>'; level--; }
    res += `<li><a href="#${h.id}">${h.html}</a></li>`;
  }
  while (level >= minL) { res += '</ul>'; level--; }
  return res;
}

function detectLang(body) {
  const prose = body.replace(/^(`{3,}|~{3,})[\s\S]*?^\1`*\s*$/gm, '');
  const han = (prose.match(/\p{Script=Han}/gu) || []).length;
  const total = (prose.match(/\S/g) || []).length || 1;
  return { lang: han / total >= 0.05 ? 'zh-CN' : 'en', han, total, ratio: +(han / total).toFixed(3) };
}

function collectImages(tokens, mdDir, warnings) {
  const byAbs = new Map();
  const takenNames = new Map();
  const missing = [];
  let remote = 0;
  const walk = (children) => {
    for (const t of children || []) {
      if (t.type === 'image') {
        const src = t.attrGet('src') || '';
        if (/^(https?:|data:|\/\/)/i.test(src)) { remote++; }
        else {
          const abs = path.resolve(mdDir, decodeURIComponent(src));
          if (!fs.existsSync(abs)) {
            missing.push(src);
            warnings.push(`local image not found: ${src}`);
          } else {
            let name = byAbs.get(abs);
            if (!name) {
              name = path.basename(abs);
              if (takenNames.has(name) && takenNames.get(name) !== abs) {
                name = crypto.createHash('sha1').update(abs).digest('hex').slice(0, 6) + '-' + name;
              }
              takenNames.set(name, abs);
              byAbs.set(abs, name);
            }
            t.attrSet('src', 'img/' + name);
          }
        }
      }
      if (t.children) walk(t.children);
    }
  };
  for (const tok of tokens) if (tok.type === 'inline') walk(tok.children);
  return {
    copies: [...byAbs.entries()].map(([from, name]) => ({ from, to: 'img/' + name })),
    missing,
    remote,
  };
}

function katexErrorTexes(html) {
  // KaTeX paints unparseable fragments with errorColor; map back to source TeX
  const errs = new Set();
  let idx = 0;
  while ((idx = html.indexOf(KATEX_ERROR_COLOR, idx)) !== -1) {
    const annStart = html.lastIndexOf('<annotation encoding="application/x-tex">', idx);
    // a total parse failure (katex-error span) has NO annotation — attributing
    // the nearest previous annotation would blame an innocent neighbor formula
    if (annStart !== -1 && !html.slice(annStart, idx).includes('katex-error')) {
      const from = html.indexOf('>', annStart) + 1;
      const to = html.indexOf('</annotation>', from);
      if (to !== -1 && to - from < 500) errs.add(decodeEntities(html.slice(from, to)));
    }
    idx += KATEX_ERROR_COLOR.length;
  }
  // also catch total parse failures (katex-error span carries title="message")
  const re = /class="katex-error"[^>]*title="([^"]*)"/g;
  let m;
  while ((m = re.exec(html))) errs.add(decodeEntities(m[1]));
  return [...errs];
}

export function buildDoc(mdPath, opts = {}) {
  const raw = fs.readFileSync(mdPath, 'utf8');
  const srcSha = crypto.createHash('sha256').update(raw).digest('hex');
  const { body, fmTitle } = stripFrontmatter(raw);
  const warnings = [];
  const figCtx = { mdDir: path.dirname(path.resolve(mdPath)), figures: [], warnings, lint: [] };
  const md = makeMd(warnings, figCtx);
  const mathLint = [...lintPlainMath(body.replace(HTML_TAG_RE, ' '), 'md'), ...figCtx.lint];

  const env = {};
  const tokens = md.parse(body, env);
  transformFigures(tokens, figCtx);

  const headings = [];
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i].type === 'heading_open') {
      headings.push({
        level: Number(tokens[i].tag.slice(1)),
        text: inlineText(tokens[i + 1]),
        html: inlineTocHtml(tokens[i + 1], warnings),
        id: tokens[i].attrGet('id'),
      });
    }
  }

  const images = collectImages(tokens, path.dirname(path.resolve(mdPath)), warnings);
  const bodyHtml = md.renderer.render(tokens, md.options, env);

  // doc-content math only: figure-internal math belongs to the figures, not these stats
  const bodyCore = exciseMarked(bodyHtml, FIG_OPEN, FIG_CLOSE);
  const annotations = extractAnnotations(bodyCore);
  const displayMath = (bodyCore.match(/katex-display/g) || []).length;
  const mathErrors = katexErrorTexes(bodyHtml); // but broken TeX anywhere is warned
  for (const tex of mathErrors) warnings.push(`math failed to render (shown in red on page): ${tex}`);

  const fences = tokens.filter((t) => t.type === 'fence'
    && !['math', 'figure', 'figure-html'].includes((t.info || '').trim().split(/\s+/)[0]));
  const codeLangs = {};
  for (const f of fences) {
    const l = (f.info || '').trim().split(/\s+/)[0] || '(none)';
    codeLangs[l] = (codeLangs[l] || 0) + 1;
  }

  const langInfo = detectLang(body);
  const title = opts.title || fmTitle
    || (headings.find((h) => h.level === 1) || {}).text
    || path.basename(mdPath).replace(/\.md$/i, '');

  const tocInner = buildToc(headings);
  const page = TEMPLATE
    .replaceAll('{{LANG}}', langInfo.lang)
    .replaceAll('{{TITLE}}', escapeHtml(title))
    .replaceAll('{{SRC_SHA}}', srcSha)
    .replaceAll('{{TOC_SIDE}}', tocInner
      ? `<nav class="toc-side toc" aria-label="Contents">${tocInner}</nav>` : '')
    .replaceAll('{{TOC_INLINE}}', tocInner
      ? `<details class="toc-inline"><summary>目录 · Contents</summary><nav class="toc">${tocInner}</nav></details>` : '')
    .replaceAll('{{BODY}}', bodyHtml);

  const hCounts = {};
  for (const h of headings) hCounts['h' + h.level] = (hCounts['h' + h.level] || 0) + 1;

  const stats = {
    title,
    lang: langInfo.lang,
    han_ratio: langInfo.ratio,
    headings: hCounts,
    math: { total: annotations.length, display: displayMath, inline: annotations.length - displayMath, render_errors: mathErrors },
    code_fences: fences.length,
    code_langs: codeLangs,
    tables: tokens.filter((t) => t.type === 'table_open').length,
    footnotes: (env.footnotes && env.footnotes.list ? env.footnotes.list.length : 0),
    images: { local: images.copies.length, remote: images.remote, missing: images.missing },
    figures: figCtx.figures,
    math_lint: { count: mathLint.length, samples: mathLint.slice(0, 30) },
    toc: tocInner ? 'yes' : 'no (fewer than 3 eligible headings)',
    page_bytes: Buffer.byteLength(page),
  };

  return { page, title, lang: langInfo.lang, srcSha, stats, warnings, images: images.copies };
}

/* ---------- CLI ---------- */

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const k = argv[i].slice(2);
      if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) { args[k] = argv[++i]; }
      else args[k] = true;
    } else args._.push(argv[i]);
  }
  return args;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = parseArgs(process.argv.slice(2));
  const input = args._[0];
  if (!input) {
    console.error('usage: node build.mjs <input.md> [--out <dir>] [--slug <s>] [--title <t>]');
    process.exit(1);
  }
  try {
    const res = buildDoc(input, { title: args.title });
    const slug = args.slug || slugifyHeading(path.basename(input).replace(/\.md$/i, ''));
    const outDir = path.join(args.out || 'out', slug);
    fs.mkdirSync(path.join(outDir, 'img'), { recursive: true });
    fs.writeFileSync(path.join(outDir, 'index.html'), res.page);
    for (const im of res.images) fs.copyFileSync(im.from, path.join(outDir, im.to));
    console.log(JSON.stringify({ status: 'success', out: path.join(outDir, 'index.html'), stats: res.stats, warnings: res.warnings }, null, 2));
  } catch (e) {
    console.log(JSON.stringify({ status: 'error', message: e.message }, null, 2));
    process.exit(1);
  }
}
