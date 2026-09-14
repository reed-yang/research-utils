/**
 * tex2svg.mjs — build-time TeX -> pure-SVG rendering via MathJax.
 *
 * Why this exists: KaTeX's output is HTML/CSS (inline-tables, absolute spans,
 * struts). Inside SVG <foreignObject>, Safari lays that out incorrectly, so
 * HTML-in-SVG can never render identically across engines. MathJax's SVG
 * output is pure geometry — glyphs as <path>, CJK (via \text{}) as native
 * <text> inheriting the page font — and reports exact dimensions, so the build
 * can place labels deterministically. Same rendering in every browser.
 */

import { mathjax } from 'mathjax-full/js/mathjax.js';
import { TeX } from 'mathjax-full/js/input/tex.js';
import { SVG } from 'mathjax-full/js/output/svg.js';
import { liteAdaptor } from 'mathjax-full/js/adaptors/liteAdaptor.js';
import { RegisterHTMLHandler } from 'mathjax-full/js/handlers/html.js';
import { AllPackages } from 'mathjax-full/js/input/tex/AllPackages.js';

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);

/* One shared document => globally unique glyph ids across all conversions */
const doc = mathjax.document('', {
  InputJax: new TeX({ packages: AllPackages }),
  OutputJax: new SVG({ fontCache: 'local', mtextInheritFont: true }),
});

/* TeX-escape a plain-text run destined for \text{…} */
export function escapeTexText(s) {
  const map = {
    '\\': '\\textbackslash ', '{': '\\{', '}': '\\}', '$': '\\$', '%': '\\%',
    '&': '\\&', '#': '\\#', '_': '\\_', '^': '\\^{}', '~': '\\~{}',
  };
  return s.replace(/[\\{}$%&#_^~]/g, (ch) => map[ch]);
}

/* Mixed label ("GEMM $6.2$ ms($31\%$)") -> single TeX string */
export function mixedTextToTex(content, { bold = false } = {}) {
  const parts = content.split(/\$([^$]+)\$/);
  let tex = '';
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) tex += parts[i]; // math segment, verbatim
    else if (parts[i]) tex += (bold ? '\\textbf{' : '\\text{') + escapeTexText(parts[i]) + '}';
  }
  return tex;
}

/**
 * Render TeX to a nested-SVG string with px dimensions.
 * Returns { svg, width, height } — svg has no x/y yet; width/height in px.
 */
export function texToSvg(tex, { fontSize = 13 } = {}) {
  const ex = fontSize * 0.5;
  const node = doc.convert(tex, { display: false, em: fontSize, ex });
  let svg = adaptor.outerHTML(adaptor.firstChild(node));
  const wEx = parseFloat((svg.match(/width="([\d.]+)ex"/) || [0, 0])[1]);
  const hEx = parseFloat((svg.match(/height="([\d.]+)ex"/) || [0, 0])[1]);
  const width = wEx * ex;
  const height = hEx * ex;
  svg = svg
    .replace(/width="[\d.]+ex"/, `width="${width.toFixed(2)}"`)
    .replace(/height="[\d.]+ex"/, `height="${height.toFixed(2)}"`);
  return { svg, width, height };
}

/**
 * Render TeX positioned inside a box (the old foreignObject geometry):
 * centered vertically, horizontal per `align`. Returns a placeable <svg …> string.
 */
export function texToPlacedSvg(tex, box, { fontSize = 13, color = '#333333', align = 'center' } = {}) {
  const { svg, width, height } = texToSvg(tex, { fontSize });
  const x = align === 'left' ? box.x
    : align === 'right' ? box.x + box.w - width
      : box.x + (box.w - width) / 2;
  const y = box.y + (box.h - height) / 2;
  return {
    svg: svg.replace(/^<svg /,
      `<svg x="${x.toFixed(2)}" y="${y.toFixed(2)}" `)
      .replace(/style="[^"]*"/, `style="color:${color}"`),
    width, height,
  };
}
