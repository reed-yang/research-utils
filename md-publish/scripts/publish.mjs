#!/usr/bin/env node
/**
 * publish.mjs — one-command orchestrator: build -> verify -> stage -> deploy.
 *
 * The ONLY command an agent needs:
 *   node publish.mjs <abs-path.md> [--slug s] [--title t] [--check] [--json]
 *   node publish.mjs --list
 *   node publish.mjs --unpublish <slug-or-md-path>
 *
 * Everything stateful lives in ~/.local/share/md-publish (shared between
 * Claude Code and Codex; never synced with the skill code):
 *   config.json    account_id / worker_name / compatibility_date
 *   manifest.json  source path -> slug (stable URLs across re-publishes)
 *   site/          the exact directory served by the Worker
 *
 * Exit codes: 0 ok · 1 usage/internal · 2 verify failed · 3 deploy failed
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const SKILL_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const STATE_DIR = process.env.MD_PUBLISH_STATE || path.join(os.homedir(), '.local', 'share', 'md-publish');
const SITE_DIR = path.join(STATE_DIR, 'site');
const CONFIG = path.join(STATE_DIR, 'config.json');
const MANIFEST = path.join(STATE_DIR, 'manifest.json');

const out = (obj, code = 0) => { console.log(JSON.stringify(obj, null, 2)); process.exit(code); };

/* ---------- deps bootstrap (first run after a fresh sync) ---------- */

function ensureDeps() {
  const require = createRequire(path.join(SKILL_DIR, 'package.json'));
  try { require.resolve('markdown-it'); return; } catch { /* install below */ }
  const r = spawnSync('npm', ['install', '--no-audit', '--no-fund', '--omit=dev'],
    { cwd: SKILL_DIR, encoding: 'utf8', timeout: 300_000 });
  if (r.status !== 0) {
    out({ status: 'error', step: 'npm-install', message: 'dependency install failed',
      stderr: (r.stderr || '').split('\n').slice(-12).join('\n') }, 1);
  }
}

/* ---------- state ---------- */

const readJson = (p, fallback) => {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return fallback; }
};
const writeJson = (p, o) => {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(o, null, 2) + '\n');
};

function loadConfig() {
  const cfg = readJson(CONFIG, null);
  if (cfg && cfg.account_id) return cfg;
  const envAcct = process.env.CLOUDFLARE_ACCOUNT_ID;
  if (envAcct) {
    const merged = { worker_name: 'mdpub', compatibility_date: '2026-08-01', ...(cfg || {}), account_id: envAcct };
    writeJson(CONFIG, merged);
    return merged;
  }
  out({
    status: 'error', step: 'config',
    message: `no Cloudflare account configured. Write ${CONFIG} as ` +
      '{"account_id":"<id>","worker_name":"mdpub","compatibility_date":"2026-08-01"} ' +
      '(list accounts with: npx wrangler whoami), or set CLOUDFLARE_ACCOUNT_ID.',
  }, 1);
}

function slugFor(absMd, requested, manifest) {
  const entry = manifest[absMd];
  if (requested) {
    const clean = requested.toLowerCase().replace(/[^a-z0-9一-鿿-]+/g, '-').replace(/^-+|-+$/g, '');
    if (!clean) out({ status: 'error', message: `unusable --slug: ${requested}` }, 1);
    return clean;
  }
  if (entry && entry.slug) return entry.slug;
  const base = path.basename(absMd).replace(/\.md$/i, '').toLowerCase()
    .replace(/[^a-z0-9一-鿿-]+/g, '-').replace(/^-+|-+$/g, '') || 'doc';
  return `${base}-${crypto.randomBytes(3).toString('hex')}`; // unguessable by default
}

/* ---------- staging ---------- */

function stageSharedAssets() {
  const nm = path.join(SKILL_DIR, 'node_modules');
  const assets = path.join(SITE_DIR, 'assets');
  fs.mkdirSync(path.join(assets, 'fonts'), { recursive: true });
  fs.copyFileSync(path.join(SKILL_DIR, 'assets', 'style.css'), path.join(assets, 'style.css'));
  fs.copyFileSync(path.join(SKILL_DIR, 'assets', 'figure-kit.css'), path.join(assets, 'figure-kit.css'));
  fs.copyFileSync(path.join(nm, 'highlight.js', 'styles', 'github.min.css'), path.join(assets, 'hljs-github.min.css'));
  // KaTeX css expects a sibling fonts/ dir
  fs.mkdirSync(path.join(assets, 'katex'), { recursive: true });
  fs.copyFileSync(path.join(nm, 'katex', 'dist', 'katex.min.css'), path.join(assets, 'katex', 'katex.min.css'));
  fs.cpSync(path.join(nm, 'katex', 'dist', 'fonts'), path.join(assets, 'katex', 'fonts'), { recursive: true });
  for (const f of ['400-normal', '400-italic', '700-normal', '700-italic']) {
    fs.copyFileSync(
      path.join(nm, '@fontsource', 'open-sans', 'files', `open-sans-latin-${f}.woff2`),
      path.join(assets, 'fonts', `open-sans-latin-${f}.woff2`),
    );
  }
  const notFound = path.join(SITE_DIR, '404.html');
  if (!fs.existsSync(notFound)) {
    fs.writeFileSync(notFound, '<!doctype html><meta charset="utf-8"><title>404</title>'
      + '<body style="font-family:sans-serif;padding:40px"><h1>404</h1><p>Not found.</p></body>\n');
  }
}

function stageDoc(build, slug) {
  const dir = path.join(SITE_DIR, slug);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(path.join(dir, 'img'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'index.html'), build.page);
  for (const im of build.images) fs.copyFileSync(im.from, path.join(dir, im.to));
}

/* ---------- deploy ---------- */

function deploy(cfg) {
  const wranglerCfg = {
    name: cfg.worker_name,
    account_id: cfg.account_id,
    compatibility_date: cfg.compatibility_date,
    workers_dev: true,
    assets: {
      directory: './site',
      html_handling: 'auto-trailing-slash',
      not_found_handling: '404-page',
    },
  };
  const cfgPath = path.join(STATE_DIR, 'wrangler.jsonc');
  writeJson(cfgPath, wranglerCfg);
  const r = spawnSync('npx', ['wrangler', 'deploy', '--config', cfgPath], {
    cwd: STATE_DIR,
    encoding: 'utf8',
    timeout: 300_000,
    env: { ...process.env, CLOUDFLARE_ACCOUNT_ID: cfg.account_id, WRANGLER_SEND_METRICS: 'false', CI: '1' },
  });
  const text = (r.stdout || '') + '\n' + (r.stderr || '');
  if (r.status !== 0) {
    return { ok: false, tail: text.trim().split('\n').slice(-15).join('\n') };
  }
  const m = text.match(/https:\/\/[\w.-]+\.workers\.dev/);
  return { ok: true, base: m ? m[0] : null, tail: null };
}

/* ---------- commands ---------- */

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const k = argv[i].slice(2);
      const flag = ['check', 'json', 'list', 'help'].includes(k);
      if (!flag && i + 1 < argv.length) args[k] = argv[++i];
      else args[k] = true;
    } else args._.push(argv[i]);
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));

if (args.help || (!args._.length && !args.list && !args.unpublish)) {
  console.log('usage: node publish.mjs <abs-path.md> [--slug s] [--title t] [--check]\n'
    + '       node publish.mjs --list\n'
    + '       node publish.mjs --unpublish <slug-or-md-path>\n'
    + '--check: build + verify + stage only (no deploy). Output is JSON on stdout.');
  process.exit(args.help ? 0 : 1);
}

const manifest = readJson(MANIFEST, {});

if (args.list) {
  const cfg = readJson(CONFIG, {});
  out({
    status: 'success',
    worker: cfg.worker_name || null,
    docs: Object.entries(manifest).map(([src, e]) => ({ source: src, slug: e.slug, url: e.url || null, last_published: e.last_published })),
  });
}

if (args.unpublish) {
  const key = Object.keys(manifest).find((k) => k === path.resolve(String(args.unpublish)) || manifest[k].slug === args.unpublish);
  if (!key) out({ status: 'error', message: `not in manifest: ${args.unpublish}` }, 1);
  const cfg = loadConfig();
  fs.rmSync(path.join(SITE_DIR, manifest[key].slug), { recursive: true, force: true });
  const gone = manifest[key]; delete manifest[key];
  writeJson(MANIFEST, manifest);
  const d = deploy(cfg);
  if (!d.ok) out({ status: 'error', step: 'deploy', message: 'redeploy after unpublish failed', wrangler_tail: d.tail }, 3);
  out({ status: 'success', unpublished: gone.slug });
}

/* -- main publish path -- */

const mdPath = path.resolve(args._[0]);
if (!fs.existsSync(mdPath)) out({ status: 'error', message: `no such file: ${mdPath}` }, 1);

ensureDeps();
const { buildDoc } = await import('./build.mjs');
const { verifyDoc } = await import('./verify.mjs');

const cfg = loadConfig();
const slug = slugFor(mdPath, args.slug, manifest);

let build;
try {
  build = buildDoc(mdPath, { title: args.title });
} catch (e) {
  out({ status: 'error', step: 'build', message: e.message }, 1);
}

const verify = verifyDoc(fs.readFileSync(mdPath, 'utf8'), build.page, { mathCount: build.stats.math.total });
if (verify.status !== 'pass') {
  out({ status: 'error', step: 'verify', message: 'fidelity gate failed — page NOT deployed',
    verify, stats: build.stats, warnings: build.warnings }, 2);
}

// persist the slug immediately (also in --check) so the URL is stable from
// the first check onward, then garbage-collect stale slug directories
manifest[mdPath] = { ...(manifest[mdPath] || {}), slug, title: build.title, src_sha256: build.srcSha };
writeJson(MANIFEST, manifest);

stageSharedAssets();
stageDoc(build, slug);

const knownSlugs = new Set(Object.values(manifest).map((e) => e.slug));
for (const d of fs.readdirSync(SITE_DIR)) {
  if (d === 'assets' || d === '404.html' || knownSlugs.has(d)) continue;
  fs.rmSync(path.join(SITE_DIR, d), { recursive: true, force: true });
}

const report = {
  status: 'success',
  mode: args.check ? 'check-only (not deployed)' : 'deployed',
  slug,
  title: build.title,
  lang: build.lang,
  staged: path.join(SITE_DIR, slug, 'index.html'),
  stats: build.stats,
  verify: { status: verify.status, prose_overlap: verify.checks.prose.overlap, warnings: verify.warnings },
  warnings: build.warnings,
};

if (args.check) {
  const prev = manifest[mdPath];
  report.url = prev && prev.url ? prev.url : `(after deploy) https://${cfg.worker_name}.<subdomain>.workers.dev/${slug}/`;
  out(report);
}

const d = deploy(cfg);
if (!d.ok) {
  out({ ...report, status: 'error', step: 'deploy', message: 'wrangler deploy failed (page staged but not live)',
    wrangler_tail: d.tail }, 3);
}

const url = d.base ? `${d.base}/${slug}/` : `https://${cfg.worker_name}.<subdomain>.workers.dev/${slug}/`;
manifest[mdPath] = {
  ...manifest[mdPath],
  url,
  first_published: (manifest[mdPath] || {}).first_published || new Date().toISOString(),
  last_published: new Date().toISOString(),
};
writeJson(MANIFEST, manifest);

report.url = url;
out(report);
