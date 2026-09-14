#!/usr/bin/env node
/**
 * fig-check.mjs — visual layout QA for figures, using headless Chromium.
 *
 * The blind spot this closes: figure authors lay out fixed boxes for content
 * (KaTeX math, CJK labels) whose rendered size they cannot know in advance.
 * This tool renders the real thing and reports, per figure:
 *   - overflows: foreignObject content larger than its box (text escapes chips)
 *   - overlaps:  pairs of text-bearing leaves whose boxes intersect
 *   - outside:   leaves that stick out of the figure's own bounds
 *   - a PNG screenshot an agent can open to SEE the figure
 *
 * usage:
 *   node fig-check.mjs <figure.svg|fragment.html>   [--shots <dir>]  # one figure
 *   node fig-check.mjs <staged-page-index.html>     [--shots <dir>]  # whole page
 * Output: JSON on stdout. Exit 0 always (quality report, not a gate).
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';
import { renderMathInHtmlFragment, transformSvgFigureMath } from './build.mjs';

const STATE_ASSETS = path.join(
  process.env.MD_PUBLISH_STATE || path.join(os.homedir(), '.local', 'share', 'md-publish'),
  'site', 'assets');

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) args[argv[i].slice(2)] = argv[++i];
    else args._.push(argv[i]);
  }
  return args;
}

/* Wrap a bare figure file the same way build.mjs would inline it */
function figurePage(figPath, warnings) {
  let inner = fs.readFileSync(figPath, 'utf8');
  if (/\.svg$/i.test(figPath)) {
    const at = inner.indexOf('<svg');
    if (at > 0) inner = inner.slice(at);
    inner = transformSvgFigureMath(inner, warnings, path.basename(figPath));
  } else {
    inner = renderMathInHtmlFragment(inner, warnings, path.basename(figPath), { output: 'html' });
  }
  return '<!doctype html>\n<html><head><meta charset="utf-8">'
    + '<link rel="stylesheet" href="/assets/style.css">'
    + '<link rel="stylesheet" href="/assets/figure-kit.css">'
    + '<link rel="stylesheet" href="/assets/katex/katex.min.css">'
    + '</head><body><article id="write" style="max-width:900px">'
    + `<figure class="mp-figure">${inner}</figure>`
    + '</article></body></html>\n';
}

const MIME = { '.html': 'text/html', '.css': 'text/css', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png' };

function serve(rootFile, assetsDir) {
  return new Promise((resolveP) => {
    const srv = http.createServer((req, res) => {
      const u = decodeURIComponent(req.url.split('?')[0]);
      let file;
      if (u.startsWith('/assets/')) file = path.join(assetsDir, u.slice('/assets/'.length));
      else if (u === '/' || u === '/index.html') file = rootFile;
      else file = path.join(path.dirname(rootFile), u.slice(1));
      if (!file || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
      res.end(fs.readFileSync(file));
    });
    srv.listen(0, '127.0.0.1', () => resolveP(srv));
  });
}

const args = parseArgs(process.argv.slice(2));
const target = path.resolve(args._[0] || '');
if (!args._[0] || !fs.existsSync(target)) {
  console.error('usage: node fig-check.mjs <figure.svg|fragment.html|page-index.html> [--shots <dir>]');
  process.exit(1);
}
const shotsDir = path.resolve(args.shots || path.join(os.tmpdir(), 'mp-figshots'));
fs.mkdirSync(shotsDir, { recursive: true });

const warnings = [];
const raw = fs.readFileSync(target, 'utf8');
const isFullPage = /^\s*<!doctype/i.test(raw);
let rootFile = target;
let tmp = null;
if (!isFullPage) {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mp-figcheck-'));
  rootFile = path.join(tmp, 'index.html');
  fs.writeFileSync(rootFile, figurePage(target, warnings));
}
if (!fs.existsSync(path.join(STATE_ASSETS, 'style.css'))) {
  console.error(`assets not staged at ${STATE_ASSETS} — run publish.mjs --check on any doc once first`);
  process.exit(1);
}

const srv = await serve(rootFile, STATE_ASSETS);
const port = srv.address().port;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 2 });
await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);

const report = await page.evaluate(() => {
  const out = [];
  const figs = [...document.querySelectorAll('.mp-figure')];
  const snip = (el) => (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 42);
  figs.forEach((fig, fi) => {
    const figBox = fig.getBoundingClientRect();
    const issues = { index: fi, overflows: [], overlaps: [], outside: [] };
    // leaves = smallest text-bearing units we can reason about
    const leaves = [
      ...fig.querySelectorAll('svg text'),
      ...fig.querySelectorAll('foreignObject > div, foreignObject > span'),
    ];
    const boxes = leaves.map((el) => {
      const r = el.getBoundingClientRect();
      return { el, r, text: snip(el) };
    }).filter((b) => b.text && b.r.width > 0);
    // 1. foreignObject content bigger than its declared box
    for (const el of fig.querySelectorAll('foreignObject')) {
      const fo = el.getBoundingClientRect();
      const kid = el.firstElementChild;
      if (!kid) continue;
      const kr = kid.getBoundingClientRect();
      // note: clientWidth is 0 on SVG elements — compare geometry rects and the
      // HTML child's scroll extent against the foreignObject's declared box
      const needW = Math.max(kr.width, kid.scrollWidth || 0);
      const needH = Math.max(kr.height, kid.scrollHeight || 0);
      if (needW > fo.width + 2 || needH > fo.height + 2) {
        issues.overflows.push({ text: snip(kid), box: `${Math.round(fo.width)}x${Math.round(fo.height)}`, needs: `${Math.round(needW)}x${Math.round(needH)}` });
      }
    }
    // 2. pairwise text collisions
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i].r, b = boxes[j].r;
        const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const iy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ix > 3 && iy > 3 && !boxes[i].el.contains(boxes[j].el) && !boxes[j].el.contains(boxes[i].el)) {
          issues.overlaps.push({ a: boxes[i].text, b: boxes[j].text, by: `${Math.round(ix)}x${Math.round(iy)}px` });
        }
      }
    }
    // 3. leaves escaping the figure bounds
    for (const b of boxes) {
      const dx = Math.max(0, Math.round(Math.max(figBox.left - b.r.left, b.r.right - figBox.right)));
      const dy = Math.max(0, Math.round(Math.max(figBox.top - b.r.top, b.r.bottom - figBox.bottom)));
      if (dx > 2 || dy > 2) issues.outside.push({ text: b.text, by: `${dx}x${dy}px` });
    }
    issues.katex_errors = fig.querySelectorAll('.katex-error').length;
    out.push(issues);
  });
  return out;
});

const figEls = await page.locator('.mp-figure').all();
for (let i = 0; i < figEls.length; i++) {
  const shot = path.join(shotsDir, `fig-${i + 1}.png`);
  await figEls[i].screenshot({ path: shot });
  report[i].screenshot = shot;
}

await browser.close();
srv.close();
if (tmp) fs.rmSync(tmp, { recursive: true, force: true });

/* Severity: overlaps/outside/katex-errors are visible defects (collision
   detection uses real rendered rects). A foreignObject whose transparent box is
   narrower than its centered content is invisible on its own — warning tier —
   but boxes should still carry ~25% width margin because render scale varies
   with the reader's viewport. */
const hard = report.reduce((n, f) => n + f.overlaps.length + f.outside.length + f.katex_errors, 0);
const soft = report.reduce((n, f) => n + f.overflows.length, 0);
console.log(JSON.stringify({
  status: hard === 0 ? 'clean' : 'issues',
  hard_issues: hard,
  box_warnings: soft,
  figures: report,
  warnings,
}, null, 2));
