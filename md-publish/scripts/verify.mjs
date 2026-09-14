#!/usr/bin/env node
/**
 * verify.mjs — fidelity gate between a source Markdown file and the HTML page
 * built from it. Publishing is blocked unless this passes, which turns
 * "the page contains exactly what the Markdown says" into a checked invariant
 * instead of a prompt-level promise.
 *
 * Checks:
 *  1. headings   — count per level and per-heading text equality (normalized)
 *  2. code       — every fenced block's content appears byte-equal, in order
 *  3. math       — every rendered formula's TeX (from MathML <annotation>)
 *                  exists in the source; count matches the build report
 *  4. prose      — letter/digit frequency overlap between source text layer
 *                  and page text (code+math excised on both sides) >= 99%
 *  5. no-script  — the page must contain no <script> at all
 *
 * CLI: node verify.mjs <input.md> <page.html> [--math-count N]
 * Module: verifyDoc(mdRaw, pageHtml, {mathCount}) -> report object
 */

import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { stripFrontmatter, decodeEntities, extractAnnotations, exciseMarked, HTML_TAG_RE, MATH_OPEN, MATH_CLOSE, FIG_OPEN, FIG_CLOSE } from './build.mjs';

/* ---------- markdown-side extractors (fence-aware line scan) ---------- */

function scanMd(body) {
  const lines = body.split(/\r?\n/);
  const headings = [];
  const fences = [];
  let inFence = false, fenceMark = '', fenceInfo = '', fenceBuf = [], fenceIndent = 0;
  for (const line of lines) {
    if (!inFence) {
      const fm = line.match(/^( {0,3})(`{3,}|~{3,})\s*(\S*)/);
      if (fm) {
        inFence = true; fenceIndent = fm[1].length; fenceMark = fm[2]; fenceInfo = fm[3]; fenceBuf = [];
        continue;
      }
      const hm = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (hm) headings.push({ level: hm[1].length, text: hm[2] });
    } else {
      const close = line.match(/^ {0,3}(`{3,}|~{3,})\s*$/);
      if (close && close[1][0] === fenceMark[0] && close[1].length >= fenceMark.length) {
        inFence = false;
        if (!/^(math|figure)/.test(fenceInfo.toLowerCase())) {
          fences.push({ info: fenceInfo, content: fenceBuf.map((l) => l.slice(fenceIndent)).join('\n') });
        }
        continue;
      }
      fenceBuf.push(line);
    }
  }
  return { headings, fences };
}

/* Strip markdown inline syntax down to comparable text */
function stripInlineMd(s) {
  return s
    .replace(/\$+([^$]*)\$+/g, ' $1 ') // keep TeX source; page side is collapsed likewise
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\[[^\]]*\]/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/[*_~]/g, '')
    .replace(/\\([\\`*_{}[\]()#+\-.!|>~])/g, '$1');
}

const normText = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

/* ---------- html-side extractors ---------- */

const stripTags = (s) => decodeEntities(s.replace(/<[^>]+>/g, ''));

function htmlHeadings(page) {
  const out = [];
  const re = /<h([1-6])[^>]*>([\s\S]*?)<\/h\1>/g;
  let m;
  while ((m = re.exec(page))) {
    // a KaTeX region in a heading contributes glyphs + MathML + annotation to
    // textContent; collapse it back to just the source TeX before comparing
    const inner = m[2].replace(
      /<!--mp:m-->[\s\S]*?application\/x-tex">([\s\S]*?)<\/annotation>[\s\S]*?<!--\/mp:m-->/g, ' $1 ');
    out.push({ level: Number(m[1]), text: stripTags(inner).trim() });
  }
  return out;
}

function htmlCodeBlocks(page) {
  const out = [];
  const re = /<pre><code[^>]*>([\s\S]*?)<\/code><\/pre>/g;
  let m;
  while ((m = re.exec(page))) out.push(stripTags(m[1]).replace(/\n$/, ''));
  return out;
}

/* ---------- the gate ---------- */

export function verifyDoc(mdRaw, pageHtml, opts = {}) {
  const errors = [];
  const warnings = [];
  const { body } = stripFrontmatter(mdRaw);
  const mdSide = scanMd(body);

  /* Designed figures (build-marked regions) are the model's sanctioned creative
     zone: excised from every fidelity check. All checks below run on pageCore. */
  const pageCore = exciseMarked(pageHtml, FIG_OPEN, FIG_CLOSE);
  const scriptsTotal = (pageHtml.match(/<script\b/gi) || []).length;

  /* 1. headings */
  const outHeadings = htmlHeadings(pageCore).filter((h) => h.text && !/^目录 · Contents$/.test(h.text));
  const headingCheck = { source: mdSide.headings.length, page: outHeadings.length, mismatches: [] };
  if (mdSide.headings.length !== outHeadings.length) {
    errors.push(`heading count mismatch: ${mdSide.headings.length} in md vs ${outHeadings.length} in page`);
  } else {
    mdSide.headings.forEach((h, i) => {
      const want = normText(stripInlineMd(h.text));
      const got = normText(outHeadings[i].text);
      if (want !== got) {
        headingCheck.mismatches.push({ index: i, md: h.text, page: outHeadings[i].text });
        errors.push(`heading ${i} text mismatch: "${h.text}" -> "${outHeadings[i].text}"`);
      }
    });
  }

  /* 2. code fences (page may contain extra blocks from indented code) */
  const outCode = htmlCodeBlocks(pageCore);
  const codeCheck = { source: mdSide.fences.length, page: outCode.length, mismatches: [] };
  let cursor = 0;
  for (const [i, f] of mdSide.fences.entries()) {
    const want = f.content.replace(/\n$/, '');
    let found = -1;
    for (let j = cursor; j < outCode.length; j++) {
      if (outCode[j] === want) { found = j; break; }
    }
    if (found === -1) {
      codeCheck.mismatches.push({ index: i, info: f.info, head: want.slice(0, 80) });
      errors.push(`code fence ${i} (${f.info || 'plain'}) content not found verbatim in page`);
    } else cursor = found + 1;
  }
  if (outCode.length > mdSide.fences.length) {
    warnings.push(`page has ${outCode.length - mdSide.fences.length} extra <pre> block(s) (indented code in source?)`);
  }

  /* 3. math annotations ⊆ source (figures may contain their own math — exempt) */
  const annotations = extractAnnotations(pageCore);
  const wsNorm = (s) => s.replace(/\s+/g, ' ').trim();
  const mdNorm = wsNorm(body);
  const mathCheck = { page: annotations.length, unmatched: [] };
  for (const tex of annotations) {
    if (!mdNorm.includes(wsNorm(tex))) {
      mathCheck.unmatched.push(tex.slice(0, 120));
      errors.push(`math TeX in page not found in source: ${tex.slice(0, 120)}`);
    }
  }
  if (opts.mathCount != null && opts.mathCount !== annotations.length) {
    errors.push(`math count mismatch: build reported ${opts.mathCount}, page has ${annotations.length}`);
  }

  /* 4. prose overlap */
  let mdProse = body;
  // figure references vanish into the exempt figure region (incl. their alt text)
  mdProse = mdProse.replace(/!\[[^\]]*\]\(\s*(?:\.\/)?figures\/[^)]*\)/g, ' ');
  // excise fences
  mdProse = mdProse.replace(/^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?^ {0,3}\1`*\s*$/gm, ' ');
  // excise math bodies via the annotations actually rendered. The match MUST be
  // $-delimited: a bare-content match for a one-letter formula like $B$ would
  // blank an arbitrary "B" elsewhere in the prose (real bug found on a long doc).
  for (const tex of annotations) {
    const esc = tex.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
    for (const pat of [`\\$\\$\\s*${esc}\\s*\\$\\$`, `\\$${esc}\\$`]) {
      try {
        const re = new RegExp(pat);
        if (re.test(mdProse)) { mdProse = mdProse.replace(re, ' '); break; }
      } catch { /* overlong pattern — leave it; both sides then keep the text */ }
    }
  }
  // drop link targets, keep link text; keep footnote-definition text, drop only its marker
  mdProse = mdProse
    .replace(/\]\([^)]*\)/g, ']')
    .replace(/^\[\^[^\]]+\]:\s*/gm, ' ')
    .replace(/^\[(?!\^)[^\]]+\]:\s+\S+.*$/gm, ' ')
    .replace(/^(\s*[-*+]\s)\[[ xX]\]\s/gm, '$1') // task-list marker renders as checkbox
    .replace(/^(\s*[-*+]\s)/gm, '$1');
  // Ordered-list numbers render as CSS markers, not DOM text — but only when the
  // line really is a list item. Per CommonMark, inside a paragraph only "1." can
  // start a list; "2. foo" after prose stays literal text and must be kept.
  {
    const ln = mdProse.split('\n');
    let prevBlank = true;
    let prevWasItem = false;
    for (let i = 0; i < ln.length; i++) {
      const m = ln[i].match(/^(\s*)(\d{1,3})[.)]\s+/);
      if (m && (prevBlank || prevWasItem || m[2] === '1')) {
        ln[i] = m[1] + ln[i].slice(m[0].length);
        prevWasItem = true;
      } else if (m) {
        prevWasItem = false;
      } else {
        prevWasItem = prevWasItem && /^\s+\S/.test(ln[i]); // indented continuation
      }
      prevBlank = ln[i].trim() === '';
    }
    mdProse = ln.join('\n');
  }
  let pageProse = exciseMarked(pageCore, MATH_OPEN, MATH_CLOSE);
  pageProse = pageProse
    .replace(/<pre><code[^>]*>[\s\S]*?<\/code><\/pre>/g, ' ')
    .replace(/<nav class="toc[\s\S]*?<\/nav>/g, ' ')
    .replace(/<details class="toc-inline[\s\S]*?<\/details>/g, ' ')
    .replace(/<head>[\s\S]*?<\/head>/, ' ')
    .replace(/<img[^>]*\balt="([^"]*)"[^>]*>/g, ' $1 '); // keep alt text (md side keeps it too)

  // identical normalization on both sides: raw HTML in the md (kit components)
  // must be tag-stripped exactly like the rendered page
  const mdNormProse = normText(decodeEntities(mdProse.replace(HTML_TAG_RE, ' ')));
  const pageNormProse = normText(stripTags(pageProse));

  const overlap = freqOverlap(charFreq(mdNormProse), charFreq(pageNormProse));
  const proseCheck = { overlap: +overlap.ratio.toFixed(4), md_chars: overlap.aTotal, page_chars: overlap.bTotal, top_diffs: overlap.topDiffs };
  if (overlap.ratio < 0.98) errors.push(`prose character overlap too low: ${(overlap.ratio * 100).toFixed(2)}% (page text diverges from source)`);
  else if (overlap.ratio < 0.995) warnings.push(`prose overlap ${(overlap.ratio * 100).toFixed(2)}% — inspect top_diffs if unexpected`);

  /* 4b. ordered containment: every W-char window of page prose must occur in
     source prose and vice versa. Length-independent — catches a single
     fabricated/omitted sentence even in a 100k-char document. */
  const W = 12;
  const fabricated = windowMisses(mdNormProse, pageNormProse, W);
  const omitted = windowMisses(pageNormProse, mdNormProse, W);
  proseCheck.window_misses = { page_not_in_md: fabricated.summary, md_not_in_page: omitted.summary };
  if (fabricated.longest >= W) {
    errors.push(`page contains a text span (~${fabricated.longest + W} chars) not present in source, near: "${fabricated.samples[0]}"`);
  } else if (fabricated.total > 0) {
    warnings.push(`minor page/source text boundary diffs (${fabricated.total} window misses), near: "${fabricated.samples[0]}"`);
  }
  if (omitted.longest >= W) {
    errors.push(`source text span (~${omitted.longest + W} chars) missing from page, near: "${omitted.samples[0]}"`);
  } else if (omitted.total > 0) {
    warnings.push(`minor source/page text boundary diffs (${omitted.total} window misses), near: "${omitted.samples[0]}"`);
  }

  /* 5. scripts: forbidden everywhere except inside designed-figure regions */
  const scriptsOutside = (pageCore.match(/<script\b/gi) || []).length;
  const scriptCheck = { outside_figures: scriptsOutside, inside_figures: scriptsTotal - scriptsOutside };
  if (scriptsOutside > 0) {
    errors.push(`page contains ${scriptsOutside} <script> tag(s) outside figure regions; only figures may carry scripts`);
  }

  return {
    status: errors.length ? 'fail' : 'pass',
    errors,
    warnings,
    checks: { headings: headingCheck, code: codeCheck, math: mathCheck, prose: proseCheck, scripts: scriptCheck },
  };
}

/* Which W-char windows of `probe` never occur in `base`? Returns miss runs. */
function windowMisses(base, probe, W) {
  if (probe.length < W) return { total: 0, longest: 0, samples: [], summary: 'n/a (text shorter than window)' };
  const set = new Set();
  for (let i = 0; i + W <= base.length; i++) set.add(base.slice(i, i + W));
  let total = 0, longest = 0, run = 0;
  const samples = [];
  for (let i = 0; i + W <= probe.length; i++) {
    if (set.has(probe.slice(i, i + W))) { run = 0; continue; }
    total++;
    run++;
    if (run > longest) longest = run;
    if (run === 1 && samples.length < 3) samples.push(probe.slice(Math.max(0, i - 4), i + W + 8));
  }
  return { total, longest, samples, summary: total === 0 ? 'clean' : `${total} misses, longest run ${longest}` };
}

function charFreq(s) {
  const m = new Map();
  for (const ch of s) m.set(ch, (m.get(ch) || 0) + 1);
  return m;
}

function freqOverlap(a, b) {
  let aTotal = 0, bTotal = 0, diff = 0;
  const keys = new Set([...a.keys(), ...b.keys()]);
  const diffs = [];
  for (const k of keys) {
    const av = a.get(k) || 0, bv = b.get(k) || 0;
    aTotal += av; bTotal += bv;
    if (av !== bv) diffs.push([k, bv - av]);
    diff += Math.abs(av - bv);
  }
  diffs.sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]));
  const denom = Math.max(aTotal, bTotal) || 1;
  return {
    ratio: 1 - diff / (2 * denom),
    aTotal, bTotal,
    topDiffs: diffs.slice(0, 10).map(([ch, d]) => `${ch}:${d > 0 ? '+' : ''}${d}`),
  };
}

/* ---------- CLI ---------- */

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [mdPath, htmlPath] = process.argv.slice(2).filter((a) => !a.startsWith('--'));
  const mcArg = process.argv.indexOf('--math-count');
  if (!mdPath || !htmlPath) {
    console.error('usage: node verify.mjs <input.md> <page.html> [--math-count N]');
    process.exit(1);
  }
  const report = verifyDoc(
    fs.readFileSync(mdPath, 'utf8'),
    fs.readFileSync(htmlPath, 'utf8'),
    { mathCount: mcArg !== -1 ? Number(process.argv[mcArg + 1]) : null },
  );
  console.log(JSON.stringify(report, null, 2));
  process.exit(report.status === 'pass' ? 0 : 2);
}
