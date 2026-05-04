/**
 * 8_post_note.mjs
 *
 * note.com の内部API を使って下書きを作成する（画像アップロード対応）。
 *
 * フロー:
 *   1. POST /api/v1/text_notes                          → note ID を取得
 *   2. 各画像: presigned POST取得 → S3アップロード → 公開URL取得
 *   3. Markdown内の ![SYMBOL](path) を <img src="公開URL"> に置換
 *   4. POST /api/v1/text_notes/draft_save?id={ID}       → HTML本文でドラフト保存
 *
 * 環境変数:
 *   NOTE_SESSION_COOKIE : _note_session_v5 の値（必須）
 */

import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { marked } from 'marked';

// ─── .env 読み込み（ローカルテスト用） ────────────────────────────────
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

const ARTICLE_MD    = 'note-article/article.md';
const META_JSON     = 'note-article/meta.json';
const EYECATCH_PATH = 'charts/title/title1.png';

for (const f of [ARTICLE_MD, META_JSON]) {
  if (!fs.existsSync(f)) {
    console.error(`ERROR: ${f} not found. Run 7_generate_note_article.py first.`);
    process.exit(1);
  }
}

const meta   = JSON.parse(fs.readFileSync(META_JSON, 'utf8'));
const bodyMd = fs.readFileSync(ARTICLE_MD, 'utf8');

// ─── 共通ヘッダー ─────────────────────────────────────────────────────
const BASE_HEADERS = {
  'X-Requested-With': 'XMLHttpRequest',
  'Origin':           'https://editor.note.com',
  'Referer':          'https://editor.note.com/',
  'Cookie':           `_note_session_v5=${SESSION_COOKIE}`,
};

// ─── PNGサイズ取得（ヘッダーから直読み、追加ライブラリ不要） ──────────
function getPngDimensions(filePath) {
  const buf = fs.readFileSync(filePath);
  // PNG仕様: バイト16-19=幅, 20-23=高さ（ビッグエンディアン）
  return {
    width:  buf.readUInt32BE(16),
    height: buf.readUInt32BE(20),
  };
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
    method:  'POST',
    headers: BASE_HEADERS,
    body:    form,
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
/**
 * ローカルのPNGをnote.comにアップロードし、公開URLを返す。
 * 失敗時は null を返す（記事投稿は継続）。
 */
async function uploadImage(localPath) {
  if (!fs.existsSync(localPath)) {
    console.warn(`  [WARN] Image not found, skipping: ${localPath}`);
    return null;
  }

  const filename = path.basename(localPath);

  // Step A: presigned POST ポリシーを取得
  const form1 = new FormData();
  form1.append('filename', filename);

  const presignedRes = await fetch('https://note.com/api/v3/images/upload/presigned_post', {
    method:  'POST',
    headers: BASE_HEADERS,   // Content-Type は FormData が自動設定
    body:    form1,
  });

  if (!presignedRes.ok) {
    console.warn(`  [WARN] presigned_post failed: HTTP ${presignedRes.status}`);
    return null;
  }

  const { data } = await presignedRes.json();
  const publicUrl  = data.url;        // https://assets.st-note.com/img/...
  const s3Endpoint = data.action;     // https://[bucket].s3.amazonaws.com
  const postFields = data.post;       // AWS署名フィールド群

  // Step B: S3 に直接アップロード
  const form2 = new FormData();
  for (const [key, val] of Object.entries(postFields)) {
    form2.append(key, val);
  }
  const fileBuffer = fs.readFileSync(localPath);
  form2.append('file', new Blob([fileBuffer], { type: 'image/png' }), filename);

  const s3Res = await fetch(s3Endpoint, {
    method: 'POST',
    body:   form2,
    // S3はnote.comのCookieを必要としない
  });

  if (!s3Res.ok && s3Res.status !== 204) {
    console.warn(`  [WARN] S3 upload failed: HTTP ${s3Res.status}`);
    return null;
  }

  console.log(`  [OK] Uploaded: ${filename} → ${publicUrl}`);
  return publicUrl;
}

// ─── Markdown内の画像マーカーを公開URLに置換 ─────────────────────────
/**
 * article.md の ![ALT](localPath) を <img src="公開URL" alt="ALT"> に変換する。
 * アップロード失敗の場合はマーカーを削除（空文字に置換）。
 */
async function resolveImages(md) {
  const imageRe = /!\[([^\]]*)\]\(([^)]+)\)/g;
  const matches = [...md.matchAll(imageRe)];

  if (matches.length === 0) return md;

  console.log(`Uploading ${matches.length} image(s)...`);
  let result = md;

  for (const m of matches) {
    const [full, alt, localPath] = m;
    const publicUrl = await uploadImage(localPath);
    if (publicUrl) {
      result = result.replace(full, `<img src="${publicUrl}" alt="${alt}">`);
    } else {
      result = result.replace(full, '');  // アップロード失敗時は削除
    }
  }

  return result;
}

// ─── Step 1: note を新規作成して ID を取得 ────────────────────────────
console.log('Step 1: Creating new note...');
const createRes = await fetch('https://note.com/api/v1/text_notes', {
  method:  'POST',
  headers: { ...BASE_HEADERS, 'Content-Type': 'application/json' },
  body:    JSON.stringify({ template_key: null }),
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

// ─── Step 1.5: アイキャッチ画像をアップロード ────────────────────────
console.log('Step 1.5: Uploading eye-catch image...');
await uploadEyecatch(noteId, EYECATCH_PATH);

// ─── 目次生成 ─────────────────────────────────────────────────────────
/**
 * HTML内のすべての<h2>タグにUUIDを付与し、冒頭に<ol>目次を挿入する。
 * note.com エディタは UUID形式の id/name 属性を使用する。
 */
function injectToc(html) {
  // h2 タグを検索し、各見出しにUUIDを割り当てる
  const headings = [];
  const htmlWithIds = html.replace(/<h2[^>]*>([\s\S]*?)<\/h2>/gi, (match, inner) => {
    const uuid = randomUUID();
    const text = inner.replace(/<[^>]+>/g, '').trim(); // タグを除去したテキスト
    headings.push({ uuid, text });
    return `<h2 name="${uuid}" id="${uuid}">${inner}</h2>`;
  });

  if (headings.length === 0) return html;

  // 目次HTMLを生成
  const tocItems = headings
    .map(({ uuid, text }) => `<li><a href="#${uuid}">${text}</a></li>`)
    .join('');
  const tocHtml = `<h2>目次</h2><ol>${tocItems}</ol>`;

  return tocHtml + htmlWithIds;
}

// ─── Step 2: 画像をアップロードしてMarkdownのパスを公開URLに差し替え ──
const resolvedMd = await resolveImages(bodyMd);

// Markdown → HTML 変換
const rawHtml  = marked.parse(resolvedMd, { gfm: true, breaks: false });
const bodyHtml = injectToc(rawHtml);

// ─── Step 3: 下書き保存 ───────────────────────────────────────────────
console.log('Step 3: Saving draft...');
const saveRes = await fetch(
  `https://note.com/api/v1/text_notes/draft_save?id=${noteId}&is_temp_saved=false`,
  {
    method:  'POST',
    headers: { ...BASE_HEADERS, 'Content-Type': 'application/json' },
    body:    JSON.stringify({
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

const draftUrl = `https://note.com/n/${noteKey}`;
console.log('DRAFT_URL=' + draftUrl);
console.log('Done. Open the draft URL to review and publish.');
