/**
 * 8_post_note.mjs
 *
 * note.com の内部API を使って下書きを作成する。
 *
 * フロー:
 *   1. POST /api/v1/text_notes          → note ID を取得
 *   2. POST /api/v1/text_notes/draft_save?id={ID}  → HTML本文でドラフト保存
 *
 * 環境変数:
 *   NOTE_SESSION_COOKIE : _note_session_v5=xxx の値（必須）
 *
 * Cookie取得方法:
 *   DevTools → Network → 任意のリクエスト → Cookie ヘッダーの
 *   _note_session_v5=xxx の値をコピー
 */

import fs from 'fs';
import path from 'path';
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
  console.error('Set it to the value of _note_session_v5 cookie from DevTools.');
  process.exit(1);
}

const ARTICLE_MD = 'note-article/article.md';
const META_JSON  = 'note-article/meta.json';

for (const f of [ARTICLE_MD, META_JSON]) {
  if (!fs.existsSync(f)) {
    console.error(`ERROR: ${f} not found. Run 7_generate_note_article.py first.`);
    process.exit(1);
  }
}

const meta = JSON.parse(fs.readFileSync(META_JSON, 'utf8'));
const bodyMd = fs.readFileSync(ARTICLE_MD, 'utf8');

// Markdown → HTML 変換
const bodyHtml = marked.parse(bodyMd, { gfm: true, breaks: false });

// ─── 共通ヘッダー ─────────────────────────────────────────────────────
const HEADERS = {
  'Content-Type':    'application/json',
  'X-Requested-With': 'XMLHttpRequest',
  'Origin':          'https://editor.note.com',
  'Referer':         'https://editor.note.com/',
  'Cookie':          `_note_session_v5=${SESSION_COOKIE}`,
};

// ─── Step 1: note を新規作成して ID を取得 ────────────────────────────
console.log('Step 1: Creating new note...');
const createRes = await fetch('https://note.com/api/v1/text_notes', {
  method: 'POST',
  headers: HEADERS,
  body: JSON.stringify({ template_key: null }),
});

if (!createRes.ok) {
  const t = await createRes.text();
  console.error(`Step 1 failed: HTTP ${createRes.status}`);
  console.error(t);
  process.exit(1);
}

const createData = await createRes.json();
const noteId  = createData?.data?.id;
const noteKey = createData?.data?.key;

if (!noteId) {
  console.error('Step 1: could not extract note ID from response:', JSON.stringify(createData));
  process.exit(1);
}
console.log(`Step 1 OK: id=${noteId}, key=${noteKey}`);

// ─── Step 2: 下書き保存 ───────────────────────────────────────────────
console.log('Step 2: Saving draft...');
const saveUrl = `https://note.com/api/v1/text_notes/draft_save?id=${noteId}&is_temp_saved=false`;

const saveRes = await fetch(saveUrl, {
  method: 'POST',
  headers: HEADERS,
  body: JSON.stringify({
    name:         meta.title,
    body:         bodyHtml,
    body_length:  bodyHtml.length,
    index:        false,
    is_lead_form: false,
  }),
});

if (!saveRes.ok) {
  const t = await saveRes.text();
  console.error(`Step 2 failed: HTTP ${saveRes.status}`);
  console.error(t);
  process.exit(1);
}

const saveData = await saveRes.json();
console.log('Step 2 OK:', JSON.stringify(saveData?.data ?? saveData));

const draftUrl = `https://note.com/n/${noteKey}`;
console.log('DRAFT_URL=' + draftUrl);
console.log('Open the draft URL to review and publish.');
