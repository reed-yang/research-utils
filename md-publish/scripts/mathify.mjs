#!/usr/bin/env node
/**
 * mathify.mjs — conservative, deterministic converter that wraps plain-text
 * math symbols in $…$ so they render through KaTeX (site rule: no plain-text
 * math anywhere). Complements the build report's math_lint.
 *
 * Only high-confidence patterns are rewritten; everything else is left for a
 * human/agent to fix with targeted edits. Fenced code, inline code, existing
 * math spans, URLs and HTML tags are never touched.
 *
 * CLI: node mathify.mjs <input.md>          # dry run: prints changed lines
 *      node mathify.mjs <input.md> --apply  # writes in place
 */

import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

const GREEK = {
  'α': '\\alpha', 'β': '\\beta', 'γ': '\\gamma', 'δ': '\\delta', 'ε': '\\varepsilon',
  'ζ': '\\zeta', 'η': '\\eta', 'θ': '\\theta', 'ι': '\\iota', 'κ': '\\kappa',
  'λ': '\\lambda', 'μ': '\\mu', 'ν': '\\nu', 'ξ': '\\xi', 'π': '\\pi', 'ρ': '\\rho',
  'σ': '\\sigma', 'τ': '\\tau', 'υ': '\\upsilon', 'φ': '\\varphi', 'χ': '\\chi',
  'ψ': '\\psi', 'ω': '\\omega',
  'Γ': '\\Gamma', 'Δ': '\\Delta', 'Θ': '\\Theta', 'Λ': '\\Lambda', 'Ξ': '\\Xi',
  'Π': '\\Pi', 'Σ': '\\Sigma', 'Υ': '\\Upsilon', 'Φ': '\\Phi', 'Ψ': '\\Psi', 'Ω': '\\Omega',
};
const SUPS = { '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4', '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9' };
const SUBS = { '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4', '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9' };
const NUM = '\\d+(?:\\.\\d+)?';

function transformLine(line) {
  // mask regions that must never be rewritten
  const masks = [];
  const mask = (re) => { line = line.replace(re, (m) => { masks.push(m); return `${masks.length - 1}`; }); };
  mask(/\$\$[^$]*\$\$/g);
  mask(/\$[^$\n]+\$/g);
  mask(/`[^`]*`/g);
  mask(/<[^>]+>/g);           // html tags incl. attributes
  mask(/\]\([^)]*\)/g);       // link/image targets
  mask(/https?:\/\/\S+/g);

  const pctSafe = (s) => s.replace(/%/g, '\\%');
  const B = '(?<![\\w.$])'; // never grab the digits out of tokens like P99

  // µs / μs after a number — BEFORE comparisons so "≈ 0.8 μs" keeps its unit
  line = line.replace(new RegExp(`${B}(${NUM})\\s*[μµ]s(?![\\w])`, 'g'), (_, a) => `$${a}\\,\\mu s$`);

  // N×M×K chains first, then N×M / N× / ×N (incl. ranges like 27–46×)
  line = line.replace(new RegExp(`${B}(${NUM})\\s*×\\s*(${NUM})\\s*×\\s*(${NUM})`, 'g'),
    (_, a, b, c) => `$${a} \\times ${b} \\times ${c}$`);
  line = line.replace(new RegExp(`${B}(${NUM})\\s*×\\s*(${NUM})`, 'g'), (_, a, b) => `$${a} \\times ${b}$`);
  line = line.replace(new RegExp(`${B}(${NUM}(?:\\s*[–-]\\s*${NUM})?)\\s*×(?![\\w])`, 'g'),
    (_, a) => `$${a.replace(/\s*–\s*/, '\\text{–}')}\\times$`);
  line = line.replace(new RegExp(`×\\s*(${NUM})`, 'g'), (_, a) => `$\\times ${a}$`);

  // comparisons around numbers (keeps % as \%)
  line = line.replace(new RegExp(`${B}(${NUM}%?)\\s*([≤≥≈≠±])\\s*(${NUM}%?)`, 'g'),
    (_, a, op, b) => `$${pctSafe(a)} ${{ '≤': '\\le', '≥': '\\ge', '≈': '\\approx', '≠': '\\neq', '±': '\\pm' }[op]} ${pctSafe(b)}$`);
  line = line.replace(new RegExp(`([≤≥≈≠±])\\s*${B.slice(0)}(${NUM}%?)`, 'g'),
    (_, op, b) => `$${{ '≤': '\\le', '≥': '\\ge', '≈': '\\approx', '≠': '\\neq', '±': '\\pm' }[op]} ${pctSafe(b)}$`);
  line = line.replace(/[≤≥≈≠±]/g, (op) => `$${{ '≤': '\\le', '≥': '\\ge', '≈': '\\approx', '≠': '\\neq', '±': '\\pm' }[op]}$`);

  // unicode minus between numbers
  line = line.replace(new RegExp(`${B}(${NUM})−(${NUM})`, 'g'), (_, a, b) => `$${a}-${b}$`);

  // super/subscript runs attached to a token
  line = line.replace(/([A-Za-z0-9]+)([⁰¹²³⁴⁵⁶⁷⁸⁹]+)/g,
    (_, base, sup) => `$${base}^{${[...sup].map((c) => SUPS[c]).join('')}}$`);
  line = line.replace(/([A-Za-z]+)([₀₁₂₃₄₅₆₇₈₉]+)/g,
    (_, base, sub) => `$${base}_{${[...sub].map((c) => SUBS[c]).join('')}}$`);

  // lone greek letters (word-boundary-ish: not inside latin words like μs — handled
  // above); a trailing * becomes a superscript star (γ* → $\gamma^*$)
  line = line.replace(/(?<![\w\\$])([αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩ])(\*)?(?![\w$])/gu,
    (_, g, star) => `$${GREEK[g]}${star ? '^*' : ''}$`);

  // misc lone symbols
  line = line.replace(/∞/g, '$\\infty$').replace(/∈/g, '$\\in$')
    .replace(/√\s*(\w+)/g, (_, a) => `$\\sqrt{${a}}$`);

  // pull single-letter operands into an adjacent lone-operator span: I$\approx$N -> $I \approx N$
  line = line.replace(/(?<![\w$\\])([A-Za-z])\$(\\approx|\\neq|\\le|\\ge|\\pm|\\times)\$([A-Za-z])(?![\w$])/g,
    '$$$1 $2 $3$$');
  // merge adjacent math fragments: "$\alpha$ $\le$" -> "$\alpha \le$", "$a$$b$" -> "$a b$",
  // and "$\pi$/$\beta$" -> "$\pi/\beta$"
  let prev;
  do {
    prev = line;
    line = line.replace(/\$([^$\n]+)\$([ \t]+|[ \t]*[/=][ \t]*)\$([^$\n]+)\$/g, '$$$1$2$3$$');
    line = line.replace(/\$([^$\n]+)\$\$([^$\n]+)\$(?!\$)/g, '$$$1 $2$$');
  } while (line !== prev);

  return line.replace(/(\d+)/g, (_, i) => masks[+i]);
}

export function mathify(src) {
  const lines = src.split('\n');
  const changes = [];
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    if (/^ {0,3}(`{3,}|~{3,})/.test(lines[i])) { inFence = !inFence; continue; }
    if (inFence) continue;
    const out = transformLine(lines[i]);
    if (out !== lines[i]) { changes.push({ line: i + 1, old: lines[i], new: out }); lines[i] = out; }
  }
  return { text: lines.join('\n'), changes };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const file = process.argv[2];
  const apply = process.argv.includes('--apply');
  if (!file) { console.error('usage: node mathify.mjs <input.md> [--apply]'); process.exit(1); }
  const src = fs.readFileSync(file, 'utf8');
  const { text, changes } = mathify(src);
  if (apply && changes.length) {
    fs.writeFileSync(file + '.mathify-bak', src); // review/undo copy — delete when satisfied
    fs.writeFileSync(file, text);
  }
  console.log(JSON.stringify({
    status: 'success',
    mode: apply ? 'applied' : 'dry-run',
    changed_lines: changes.length,
    changes: changes.slice(0, 120),
  }, null, 2));
}
