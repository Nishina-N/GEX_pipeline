/**
 * post_note_from_obsidian.mjs
 *
 * Obsidian の確定ノート（fleet note/GEX_{date}.md）を note.com の下書きとして
 * 作成する（画像アップロード対応）。8_post_note.mjs のローカル/Obsidian運用版。
 *
 * 8_post_note.mjs との違い:
 *   - 記事ソースを note-article/article.md + meta.json から
 *     Obsidian の GEX_{date}.md（フロントマター付き）に変更。
 *   - title はフロントマターから取得（meta.json 不要）。
 *   - 画像パスはノートからの相対パス（attachments/{date}/...）として解決。
 *
 * フロー（8_post_note.mjs と同一）:
 *   1. POST /api/v1/text_notes                          → note ID を取得
 *   2. 各画像: presigned POST取得 → S3アップロード → 公開URL取得
 *   3. Markdown内の ![ALT](attachments/...) を <img src="公開URL"> に置換
 *   4. POST /api/v1/text_notes/draft_save?id={ID}       → HTML本文でドラフト保存
 *
 * 使い方:
 *   node scripts/post_note_from_obsidian.mjs [--date YYYY-MM-DD] [--note <path>]
 *     --date : fleet note/GEX_{date}.md を対象にする
 *     --note : ノートのフルパスを直接指定（--date より優先）
 *     既定   : fleet note 内の最新 GEX_*.md を自動選択
 *
 * 環境変数:
 *   NOTE_SESSION_COOKIE  : _note_session_v5 の値（必須）
 *   OBSIDIAN_FLEET_DIR   : fleet note ディレクトリ（任意。既定は下記 DEFAULT_FLEET_DIR）
 */

import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { marked } from 'marked';

// ─── .env 読み込み（ローカル用） ──────────────────────────────────────
const envPath = path.resolve('.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const m = line.match(/^([^#=]+)=(.*)$/);
    if (m) process.env[m[1].trim()] = m[2].trim();
  }
}

// ─── 設定 ─────────────────────────────────────────────────────────────
const SESSION_COOKIE = process.env.NOTE_SESSION_COOKIE;
if (!SESSION_COOKIE) {
  console.error('ERROR: NOTE_SESSION_COOKIE is not set.');
  process.exit(1);
}

const DEFAULT_FLEET_DIR = 'C:/Users/nishiha/Work/Obsidian/Obsidian Vault R2/fleet note';
const FLEET_DIR = process.env.OBSIDIAN_FLEET_DIR || DEFAULT_FLEET_DIR;
const EYECATCH_PATH = 'charts/title/title1.png';

// ─── 引数パース ───────────────────────────────────────────────────────
function parseArgs() {
  const args = process.argv.slice(2);
  const out = { date: null, note: null };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--date') out.date = args[++i];
    else if (args[i] === '--note') out.note = args[++i];
  }
  return out;
}

function resolveNotePath({ date, note }) {
  if (note) return path.resolve(note);
  if (date) return path.join(FLEET_DIR, `GEX_${date}.md`);
  // 既定: fleet note 内の最新 GEX_*.md
  if (!fs.existsSync(FLEET_DIR)) {
    console.error(`ERROR: fleet dir not found: ${FLEET_DIR}`);
    process.exit(1);
  }
  const candidates = fs.readdirSync(FLEET_DIR)
    .filter(f => /^GEX_\d{4}-\d{2}-\d{2}\.md$/.test(f))
    .sort();
  if (candidates.length === 0) {
    console.error(`ERROR: no GEX_*.md found in ${FLEET_DIR}`);
    process.exit(1);
  }
  return path.join(FLEET_DIR, candidates[candidates.length - 1]);
}

const NOTE_PATH = resolveNotePath(parseArgs());
if (!fs.existsSync(NOTE_PATH)) {
  console.error(`ERROR: note not found: ${NOTE_PATH}`);
  process.exit(1);
}
const NOTE_DIR = path.dirname(NOTE_PATH);
console.log(`Source note: ${NOTE_PATH}`);

// ─── フロントマター分離 ───────────────────────────────────────────────
/**
 * 先頭の YAML フロントマター（--- ... ---）を分離し、{ meta, body } を返す。
 * meta.title / meta.tags を抽出する（tags は情報用、投稿APIには未使用）。
 */
function parseFrontmatter(raw) {
  const m = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return { meta: {}, body: raw };
  const yaml = m[1];
  const body = m[2];
  const meta = {};
  const titleMatch = yaml.match(/^title:\s*(.+)$/m);
  if (titleMatch) meta.title = titleMatch[1].trim();
  const tagsMatch = yaml.match(/^tags:\s*\[(.*)\]\s*$/m);
  if (tagsMatch) meta.tags = tagsMatch[1].split(',').map(s => s.trim()).filter(Boolean);
  return { meta, body };
}

const rawNote = fs.readFileSync(NOTE_PATH, 'utf8');
const { meta, body: bodyMd } = parseFrontmatter(rawNote);
if (!meta.title) {
  // フロントマターに title が無ければ最初の H1 を流用
  const h1 = bodyMd.match(/^#\s+(.+)$/m);
  meta.title = h1 ? h1[1].trim() : 'GEXレポート';
}
console.log(`Title: ${meta.title}`);

// ─── 共通ヘッダー ─────────────────────────────────────────────────────
const BASE_HEADERS = {
  'X-Requested-With': 'XMLHttpRequest',
  'Origin':           'https://editor.note.com',
  'Referer':          'https://editor.note.com/',
  'Cookie':           `_note_session_v5=${SESSION_COOKIE}`,
};

// ─── PNGサイズ取得 ────────────────────────────────────────────────────
function getPngDimensions(filePath) {
  const buf = fs.readFileSync(filePath);
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

// ─── アイキャッチ画像アップロード ────────────────────────────────────
async function uploadEyecatch(noteId, localPath) {
  if (!fs.existsSync(localPath)) {
    console.warn(`  [WARN] Eye-catch image not found, skipping: ${localPath}`);
    return;
  }
  const { width, height } = getPngDimensions(localPath);
  const filename   = path.basename(localPath);
  const fileBuffer = fs.readFileSync(localPath);
  const blob       = new Blob([fileBuffer], { type: 'image/png' });

  const form = new FormData();
  form.append('note_id', String(noteId));
  form.append('file',    blob, filename);
  form.append('blob',    blob, filename);
  form.append('width',   String(width));
  form.append('height',  String(height));

  const res = await fetch('https://note.com/api/v1/image_upload/note_eyecatch', {
    method: 'POST', headers: BASE_HEADERS, body: form,
  });
  if (!res.ok) {
    console.warn(`  [WARN] Eye-catch upload failed: HTTP ${res.status}\n${await res.text()}`);
    return;
  }
  const resData = await res.json();
  const eyecatchUrl = resData?.data?.url ?? resData?.url ?? resData?.data;
  console.log(`  [OK] Eye-catch uploaded: ${eyecatchUrl}`);
}

// ─── 画像アップロード ─────────────────────────────────────────────────
async function uploadImage(localPath) {
  if (!fs.existsSync(localPath)) {
    console.warn(`  [WARN] Image not found, skipping: ${localPath}`);
    return null;
  }
  const filename = path.basename(localPath);

  const form1 = new FormData();
  form1.append('filename', filename);
  const presignedRes = await fetch('https://note.com/api/v3/images/upload/presigned_post', {
    method: 'POST', headers: BASE_HEADERS, body: form1,
  });
  if (!presignedRes.ok) {
    console.warn(`  [WARN] presigned_post failed: HTTP ${presignedRes.status}`);
    return null;
  }
  const { data } = await presignedRes.json();
  const publicUrl  = data.url;
  const s3Endpoint = data.action;
  const postFields = data.post;

  const form2 = new FormData();
  for (const [key, val] of Object.entries(postFields)) form2.append(key, val);
  const fileBuffer = fs.readFileSync(localPath);
  form2.append('file', new Blob([fileBuffer], { type: 'image/png' }), filename);

  const s3Res = await fetch(s3Endpoint, { method: 'POST', body: form2 });
  if (!s3Res.ok && s3Res.status !== 204) {
    console.warn(`  [WARN] S3 upload failed: HTTP ${s3Res.status}`);
    return null;
  }
  console.log(`  [OK] Uploaded: ${filename} → ${publicUrl}`);
  return publicUrl;
}

// ─── Markdown内の画像マーカーを公開URLに置換 ─────────────────────────
/**
 * ![ALT](relPath) を <img src="公開URL" alt="ALT"> に変換する。
 * relPath はノートからの相対パス（attachments/...）として解決する。
 */
async function resolveImages(md, baseDir) {
  const imageRe = /!\[([^\]]*)\]\(([^)]+)\)/g;
  const matches = [...md.matchAll(imageRe)];
  if (matches.length === 0) return md;

  console.log(`Uploading ${matches.length} image(s)...`);
  let result = md;
  for (const m of matches) {
    const [full, alt, relPath] = m;
    const localPath = path.isAbsolute(relPath) ? relPath : path.resolve(baseDir, relPath);
    const publicUrl = await uploadImage(localPath);
    result = result.replace(full, publicUrl ? `<img src="${publicUrl}" alt="${alt}">` : '');
  }
  return result;
}

// ─── 目次生成 ─────────────────────────────────────────────────────────
function injectToc(html) {
  const headings = [];
  const htmlWithIds = html.replace(/<h2[^>]*>([\s\S]*?)<\/h2>/gi, (match, inner) => {
    const uuid = randomUUID();
    const text = inner.replace(/<[^>]+>/g, '').trim();
    headings.push({ uuid, text });
    return `<h2 name="${uuid}" id="${uuid}">${inner}</h2>`;
  });
  if (headings.length === 0) return html;
  const tocItems = headings.map(({ uuid, text }) => `<li><a href="#${uuid}">${text}</a></li>`).join('');
  return `<h2>目次</h2><ol>${tocItems}</ol>` + htmlWithIds;
}

// ─── Step 1: note を新規作成 ──────────────────────────────────────────
console.log('Step 1: Creating new note...');
const createRes = await fetch('https://note.com/api/v1/text_notes', {
  method: 'POST',
  headers: { ...BASE_HEADERS, 'Content-Type': 'application/json' },
  body: JSON.stringify({ template_key: null }),
});
if (!createRes.ok) {
  console.error(`Step 1 failed: HTTP ${createRes.status}\n${await createRes.text()}`);
  process.exit(1);
}
const createData = await createRes.json();
const noteId  = createData?.data?.id;
const noteKey = createData?.data?.key;
if (!noteId) {
  console.error('Step 1: could not extract note ID:', JSON.stringify(createData));
  process.exit(1);
}
console.log(`Step 1 OK: id=${noteId}, key=${noteKey}`);

// ─── Step 1.5: アイキャッチ ───────────────────────────────────────────
console.log('Step 1.5: Uploading eye-catch image...');
await uploadEyecatch(noteId, EYECATCH_PATH);

// ─── Step 2: 画像アップロード＋差し替え ───────────────────────────────
const resolvedMd = await resolveImages(bodyMd, NOTE_DIR);
const rawHtml  = marked.parse(resolvedMd, { gfm: true, breaks: false });
const bodyHtml = injectToc(rawHtml);

// ─── Step 3: 下書き保存 ───────────────────────────────────────────────
console.log('Step 3: Saving draft...');
const saveRes = await fetch(
  `https://note.com/api/v1/text_notes/draft_save?id=${noteId}&is_temp_saved=false`,
  {
    method: 'POST',
    headers: { ...BASE_HEADERS, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name:         meta.title,
      body:         bodyHtml,
      body_length:  bodyHtml.length,
      index:        false,
      is_lead_form: false,
    }),
  }
);
if (!saveRes.ok) {
  console.error(`Step 3 failed: HTTP ${saveRes.status}\n${await saveRes.text()}`);
  process.exit(1);
}
const saveData = await saveRes.json();
console.log('Step 3 OK:', JSON.stringify(saveData?.data ?? saveData));
console.log('DRAFT_URL=' + `https://note.com/n/${noteKey}`);
console.log('Done. Open the draft URL to review and publish.');
