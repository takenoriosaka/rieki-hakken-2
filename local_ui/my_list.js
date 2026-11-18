// マイブランド登録（旧名: 仕入れリスト）。登録するのは ブランド・ジャンル・型番・モデル名・下限金額・上限金額・検索数 の7つ（ブランド以外は空欄OK）。保存はサーバー側の my_list.json（/api/mylist*）
// 英語のつづり・別名はツールの表から自動（サーバー側 my_list.auto_name_en / canonical_brand）。グループは画面に出さない（JSON には残る）
// research.js の後に読み込む（OPTIONS・state・cond・free・$・esc・num・yen・postResearch などを使う）

let ML = null;   // /api/mylist の内容
const ml = {
  search: '', filter: 'all', target: '', confirmDel: null,
  editId: null, editGenres: new Set(), editDraft: { name: '', min: '', max: '', numbers: '', models: '', per: '' },
  quickGenres: new Set(), ngOpen: { q: false, e: false },
  highlight: null, hlTimer: null, quickTimer: null, flashTimer: null,
};
const ML_INTRO_KEY = 'rieki-mylist-intro-collapsed-v1';
const ML_FILTERS = [['all', 'すべて'], ['on', 'オンだけ'], ['off', 'オフだけ']];
const ML_TARGET_KEY = 'rieki-mylist-target-v1';

function isListView() { return !!$('listView') && !$('listView').hidden; }
async function showList() {
  showView('list');
  mlRenderIntro();
  if (!ML) await mlLoad();
  renderList();
}
async function mlLoad() {
  try { ML = await (await fetch('/api/mylist')).json(); } catch (e) { ML = null; }
  mlRenderLinks();
}

// ── 他の画面の「マイブランド：登録 N件（オン M件）」 ─────────────
function mlRenderLinks() {
  document.querySelectorAll('[data-ml-link]').forEach(el => {
    if (!ML) { el.hidden = true; return; }
    const total = ML.entries.length, on = ML.entries.filter(e => e.enabled).length;
    el.hidden = false;
    el.innerHTML = `<span class="ml-link-text">⭐ マイブランド：登録 <b>${total}</b>件（オン <b>${on}</b>件）</span><span class="ml-link-act">✏️ 登録・編集する</span>`;
    el.setAttribute('aria-label', `マイブランド 登録${total}件 オン${on}件。登録・編集する`);
  });
}

// ── 説明カード（たたむ状態はこのブラウザに保存。保存できなくても動く） ─────────────
let mlIntroCollapsed = false;
try { mlIntroCollapsed = localStorage.getItem(ML_INTRO_KEY) === '1'; } catch (e) { mlIntroCollapsed = false; }
function mlRenderIntro() {
  if (!$('mlIntro')) return;
  const c = mlIntroCollapsed;
  $('mlIntro').classList.toggle('collapsed', c);
  $('mlIntroBody').hidden = c;
  $('mlIntroAct').textContent = c ? '説明を見る ▼' : 'たたむ ▲';
  $('mlIntroToggle').setAttribute('aria-expanded', String(!c));
}
function mlToggleIntro() {
  mlIntroCollapsed = !mlIntroCollapsed;
  try { localStorage.setItem(ML_INTRO_KEY, mlIntroCollapsed ? '1' : '0'); } catch (e) { /* なくても動く */ }
  mlRenderIntro();
}

function mlFlash(msg) {
  const el = $('mlFlash');
  if (!el) return;
  el.textContent = msg;
  clearTimeout(ml.flashTimer);
  ml.flashTimer = setTimeout(() => { el.textContent = ''; }, 4000);
}
async function mlApi(method, url, body) {
  try {
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json();
    if (data.list) ML = data.list;
    return data;
  } catch (e) {
    return { ok: false, error: 'サーバーに接続できません（start.command で起動しているか確認してください）' };
  }
}

// ── ジャンル ─────────────────────────────────────
function mlGenreLabel(tok) {
  const g = ML && ML.builtin_genres.find(x => x.key === tok);
  return g ? g.label : tok;
}
function mlGenreIcon(tok) {
  const g = ML && ML.builtin_genres.find(x => x.key === tok);
  return g ? g.icon : '🏷️';
}
function mlAllGenres() {
  if (!ML) return [];
  return ML.builtin_genres.map(g => g.key).concat(ML.custom_genres.filter(c => !ML.builtin_genres.some(g => g.key === c)));
}
// ジャンルに何が入るか（メルカリのカテゴリ名。チップにマウスを置くと出る）
function mlGenreHint(tok) {
  const g = ML && ML.builtin_genres.find(x => x.key === tok);
  return g && g.hint ? `メルカリの「${g.hint}」` : '';
}
function mlGenreQueries(tok) {
  const g = ML && ML.builtin_genres.find(x => x.key === tok);
  return g ? g.queries : 1;
}
// ジャンルのチップ（scope: 'q' = 追加の欄 / 'e' = 編集中の行）。最後に小さな「＋ ジャンルを作る」
function mlGenreChipsHtml(scope, selected) {
  const chips = mlAllGenres().map(t => {
    const onG = selected.has(t);
    return `<button type="button" class="chip ${onG ? 'on' : ''}" data-ml-genre="${scope}" data-g="${esc(t)}" aria-pressed="${onG}" title="${esc(mlGenreHint(t))}">${onG ? '✓ ' : ''}${mlGenreIcon(t)} ${esc(mlGenreLabel(t))}</button>`;
  }).join('');
  const ng = ml.ngOpen[scope]
    ? `<span class="ml-ng"><input type="text" class="text-in ml-ng-in" id="mlNg_${scope}" placeholder="例: 時計" autocomplete="off" aria-label="新しいジャンルの名前"><button type="button" class="ghost-btn ml-small" data-ml-ng-add="${scope}">追加</button><button type="button" class="link-btn ml-small" data-ml-ng-close="${scope}" aria-label="閉じる">×</button></span>`
    : `<button type="button" class="link-btn ml-ng-open" data-ml-ng-open="${scope}">＋ ジャンルを作る</button>`;
  return chips + ng;
}
async function mlAddGenre(scope) {
  const el = $(`mlNg_${scope}`);
  const label = el ? el.value.trim() : '';
  const say = msg => (scope === 'q' ? mlQuickMsg(msg, false) : ($('mlEditErr') && ($('mlEditErr').textContent = msg)));
  if (!label) { say('新しいジャンルの名前を入力してください'); if (el) el.focus(); return; }
  if (scope === 'e') mlKeepDraft();
  const r = await mlApi('POST', '/api/mylist/genre', { label });
  if (!r.ok) { say(r.error || '追加できませんでした'); return; }
  (scope === 'q' ? ml.quickGenres : ml.editGenres).add(r.genre);
  ml.ngOpen[scope] = false;
  if (scope === 'q') renderListQuick(); else renderListGroups();
}

// ── 検索・絞り込み ─────────────────────────────────
function mlNorm(s) {
  return String(s || '').normalize('NFKC').toUpperCase().replace(/[\s・･.\-_'’/／&＆]/g, '')
    .replace(/ヴァ/g, 'バ').replace(/ヴィ/g, 'ビ').replace(/ヴェ/g, 'ベ').replace(/ヴォ/g, 'ボ').replace(/ヴ/g, 'ブ').replace(/ー/g, '');
}
function mlMatches(e) {
  if (ml.filter === 'on' && !e.enabled) return false;
  if (ml.filter === 'off' && e.enabled) return false;
  const q = mlNorm(ml.search);
  if (!q) return true;
  return [e.name, e.name_en, e.brand_key, ...(e.aliases || [])].some(x => mlNorm(x).includes(q));
}

// ── 描画 ─────────────────────────────────────────
function renderList() {
  if (!$('listView')) return;
  if (!ML) { $('mlCount').textContent = 'マイブランドを読み込めませんでした（サーバーを再起動してください）'; return; }
  const total = ML.entries.length;
  const on = ML.entries.filter(e => e.enabled).length;
  $('mlCount').innerHTML = `登録 <b>${total}</b>件 ・ オン <b>${on}</b>件`;
  $('mlFilterSeg').innerHTML = ML_FILTERS.map(([k, l]) => `<button type="button" class="${ml.filter === k ? 'on' : ''}" data-ml-filter="${k}">${l}</button>`).join('');
  if (document.activeElement !== $('mlSearch')) $('mlSearch').value = ml.search;
  renderListQuick();
  renderListGroups();
  renderListResearch();
  renderListBanner();
  mlRenderLinks();
}

// ── 追加の欄（7項目） ───────────────────────────
function renderListQuick() {
  if (!$('mlQuick') || !ML) return;
  // 入力候補: ツールが知っているブランド（ブランド名のみ表示）。登録済みは除く
  const have = new Set(ML.entries.map(e => mlNorm(e.name)));
  const known = (OPTIONS && OPTIONS.free && OPTIONS.free.brands) || [];
  $('mlQuickList').innerHTML = known.filter(b => !have.has(mlNorm(b.name)))
    .map(b => `<option value="${esc(b.name)}"></option>`).join('');
  if ($('mlQuickPer') && !$('mlQuickPer').options.length) $('mlQuickPer').innerHTML = mlPerOptionsHtml('');
  const keep = $('mlNg_q') ? $('mlNg_q').value : '';
  $('mlQuickGenres').innerHTML = mlGenreChipsHtml('q', ml.quickGenres);
  if ($('mlNg_q')) $('mlNg_q').value = keep;
}
function mlQuickMsg(msg, ok) {
  const el = $('mlQuickMsg');
  el.textContent = msg;
  el.className = 'ml-quick-msg ' + (ok ? 'ok' : 'err');
  clearTimeout(ml.quickTimer);
  ml.quickTimer = setTimeout(() => { el.textContent = ''; el.className = 'ml-quick-msg'; }, ok ? 5000 : 7000);
}
function mlPriceOk(v) {
  return v === '' || (Number.isFinite(Number(v)) && Number(v) >= 0);
}
// 検索数の選び方（リサーチ画面の取得件数と同じ 少なめ/普通/多め。空＝リサーチ画面の取得件数）
function mlPerOptionsHtml(val) {
  const v = String(val || '');
  return [['', 'おまかせ（リサーチ画面の件数）']].concat(PER_SOURCE_PRESETS.map(([n, l]) => [String(n), `${l}（${n}件）`]))
    .map(([k, l]) => `<option value="${k}" ${k === v ? 'selected' : ''}>${esc(l)}</option>`).join('');
}
function mlPerLabel(n) {
  const p = PER_SOURCE_PRESETS.find(([v]) => v === Number(n));
  return p ? `${p[1]} ${n}件` : `${n}件`;
}
// 入力のチェック（追加・編集で共通）。問題なければ ''
function mlFieldsError(f) {
  if (!f.name) return 'ブランドを入力してください';
  if (!mlPriceOk(f.price_min)) return '下限金額は0以上の数字で入れてください（空欄なら下限なし）';
  if (!mlPriceOk(f.price_max)) return '上限金額は0以上の数字で入れてください（空欄なら上限なし）';
  if (f.price_min !== '' && f.price_max !== '' && Number(f.price_max) > 0 && Number(f.price_min) > Number(f.price_max)) return '下限金額が上限金額より大きくなっています';
  return '';
}
let mlQuickBusy = false;
async function mlQuickAdd() {
  if (mlQuickBusy) return;
  const f = {
    name: $('mlQuickName').value.trim(), model_numbers: $('mlQuickNumbers').value.trim(), models: $('mlQuickModels').value.trim(),
    price_min: $('mlQuickMin').value.trim(), price_max: $('mlQuickMax').value.trim(), per_source: $('mlQuickPer').value,
  };
  const name = f.name;
  const ferr = mlFieldsError(f);
  if (ferr) { mlQuickMsg(ferr, false); (name ? (ferr.startsWith('下限') ? $('mlQuickMin') : $('mlQuickMax')) : $('mlQuickName')).focus(); return; }
  if (!ML) { mlQuickMsg('マイブランドを読み込めませんでした（サーバーを再起動してください）', false); return; }
  mlQuickBusy = true;
  $('mlQuickAddBtn').disabled = true;
  const r = await mlApi('POST', '/api/mylist/entry', { ...f, genres: [...ml.quickGenres] });
  mlQuickBusy = false;
  $('mlQuickAddBtn').disabled = false;
  if (!r.ok) { mlQuickMsg(r.error || '追加できませんでした', false); return; }
  ['mlQuickName', 'mlQuickNumbers', 'mlQuickModels', 'mlQuickMin', 'mlQuickMax'].forEach(id => { $(id).value = ''; });
  $('mlQuickPer').value = '';
  ml.quickGenres = new Set();
  ml.ngOpen.q = false;
  ml.search = ''; ml.filter = 'all';
  mlHighlight(r.entry && r.entry.id);
  renderList();
  mlQuickMsg(`✓ 「${r.entry ? r.entry.name : name}」を登録しました（リサーチする: オン）`, true);
  const row = r.entry && document.querySelector(`[data-ml-row="${CSS.escape(r.entry.id)}"]`);
  if (row) row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  $('mlQuickName').focus();
}
function mlHighlight(id) {
  ml.highlight = id || null;
  clearTimeout(ml.hlTimer);
  ml.hlTimer = setTimeout(() => {
    ml.highlight = null;
    const el = document.querySelector('.ml-row.ml-new');
    if (el) el.classList.remove('ml-new');
  }, 2600);
}

function renderListBanner() {
  const b = $('lastBannerList');
  const src = $('lastBanner');
  if (!b || !src) return;
  b.hidden = src.hidden;
  b.innerHTML = src.innerHTML;
}

// ── 一覧の1行 ───────────────────────────────────
function mlPriceText(e) {
  if (e.price_min && e.price_max) return `${yen(e.price_min)}〜${yen(e.price_max)}`;
  if (e.price_min) return `${yen(e.price_min)}〜`;
  if (e.price_max) return `〜${yen(e.price_max)}`;
  return '金額指定なし';
}
// 型番・モデル名・検索数の小さなタグ（入れたものだけ）
function mlTagsHtml(e) {
  const tags = (e.models || []).map(m => `<span class="ml-tag" title="モデル名">🔖 ${esc(m)}</span>`)
    .concat((e.model_numbers || []).map(n => `<span class="ml-tag num" title="型番">${esc(n)}</span>`));
  if (e.per_source) tags.push(`<span class="ml-tag per" title="仕入れ先1か所あたりの検索数">検索数 ${esc(mlPerLabel(e.per_source))}</span>`);
  return tags.length ? `<div class="ml-tags">${tags.join('')}</div>` : '';
}
function mlEditHtml(e) {
  const d = ml.editDraft;
  return `<div class="ml-row ml-editing" data-ml-row="${esc(e.id)}">
    <div class="ml-main">
      <div class="ml-edit-grid">
        <div class="ml-edit-name">
          <label for="mlEditName" class="field-label">ブランド</label>
          <input type="text" id="mlEditName" class="text-in" list="mlQuickList" value="${esc(d.name)}" autocomplete="off" enterkeyhint="done">
        </div>
      </div>
      <div class="ml-edit-grid ml-row2">
        <div class="ml-edit-term">
          <label for="mlEditNumbers" class="field-label">型番 <small>空欄OK・複数は「、」かスペース</small></label>
          <input type="text" id="mlEditNumbers" class="text-in" placeholder="例: M41526" value="${esc(d.numbers)}" autocomplete="off" enterkeyhint="done">
        </div>
        <div class="ml-edit-term">
          <label for="mlEditModels" class="field-label">モデル名 <small>空欄OK・複数は「、」</small></label>
          <input type="text" id="mlEditModels" class="text-in" placeholder="例: スピーディ30" value="${esc(d.models)}" autocomplete="off" enterkeyhint="done">
        </div>
      </div>
      <div class="ml-edit-grid ml-row2">
        <div class="ml-edit-price">
          <label for="mlEditMin" class="field-label">下限金額 <small>空欄OK</small></label>
          <div class="num-box"><input type="number" id="mlEditMin" min="0" step="500" inputmode="numeric" placeholder="なし" value="${esc(d.min)}" enterkeyhint="done"><span>円</span></div>
        </div>
        <div class="ml-edit-price">
          <label for="mlEditMax" class="field-label">上限金額 <small>送料込み・空欄OK</small></label>
          <div class="num-box"><input type="number" id="mlEditMax" min="0" step="500" inputmode="numeric" placeholder="なし" value="${esc(d.max)}" enterkeyhint="done"><span>円</span></div>
        </div>
        <div class="ml-edit-per">
          <label for="mlEditPer" class="field-label">検索数 <small>仕入れ先1か所あたり</small></label>
          <select id="mlEditPer" class="text-in">${mlPerOptionsHtml(d.per)}</select>
        </div>
      </div>
      <div class="field-label ml-sub">ジャンル <small>選ばなければおまかせ</small></div>
      <div class="chips">${mlGenreChipsHtml('e', ml.editGenres)}</div>
      <div class="ml-form-btns">
        <button type="button" class="primary-btn ml-small" data-ml-edit-save="${esc(e.id)}">保存する</button>
        <button type="button" class="ghost-btn ml-small" data-ml-edit-cancel="1">やめる</button>
      </div>
      <div class="error-line" id="mlEditErr" role="alert"></div>
    </div>
  </div>`;
}
function mlEntryHtml(e) {
  if (ml.editId === e.id) return mlEditHtml(e);
  if (ml.confirmDel === e.id) {
    return `<div class="ml-row ml-confirm" data-ml-row="${esc(e.id)}">
      <div class="ml-main"><b>「${esc(e.name)}」を削除しますか？</b><div class="hint">削除すると元に戻せません（もう一度追加はできます）</div></div>
      <div class="ml-actions">
        <button type="button" class="primary-btn ml-danger" data-ml-del-yes="${esc(e.id)}">削除する</button>
        <button type="button" class="ghost-btn" data-ml-del-no="1">やめる</button>
      </div></div>`;
  }
  const genres = e.effective_genres || [];
  let gHtml;
  const hasTerms = (e.models || []).length || (e.model_numbers || []).length;
  if (hasTerms && !(e.genres || []).length) gHtml = '<span class="ml-omakase">ジャンル：指定なし（型番・モデル名で調べます）</span>';
  else if (!genres.length) gHtml = '<span class="ml-gnone">ジャンルなし（調べません）</span>';
  else if (e.uses_default) gHtml = `<span class="ml-omakase">ジャンル：おまかせ（${esc(genres.map(mlGenreLabel).join('・'))}）</span>`;
  else gHtml = genres.map(g => `<span class="ml-gchip" title="${esc(mlGenreHint(g))}">${mlGenreIcon(g)} ${esc(mlGenreLabel(g))}</span>`).join('');
  const sample = e.sample ? '<span class="ml-sample">見本</span>' : '';
  return `<div class="ml-row ${e.enabled ? 'is-on' : ''}${ml.highlight === e.id ? ' ml-new' : ''}" data-ml-row="${esc(e.id)}">
    <label class="ml-onoff" title="リサーチする / しない">
      <span class="ml-switch"><input type="checkbox" data-ml-toggle="${esc(e.id)}" ${e.enabled ? 'checked' : ''} aria-label="${esc(e.name)} をリサーチする"><span class="ml-slider" aria-hidden="true"></span></span>
      <span class="ml-onoff-text">${e.enabled ? 'リサーチする' : 'しない'}</span>
    </label>
    <div class="ml-main">
      <div class="ml-name">${esc(e.name)}${sample}<span class="ml-price${(e.price_min || e.price_max) ? ' has' : ''}" title="${(e.price_min || e.price_max) ? 'このブランドの仕入れ値の範囲（送料込み）。空欄の側はリサーチ画面の範囲' : 'リサーチ画面の仕入れ値の範囲で調べます'}">${mlPriceText(e)}</span></div>
      <div class="ml-genres">${gHtml}</div>
      ${mlTagsHtml(e)}
      ${e.sample ? '<div class="ml-sample-hint">見本として入れてあります。消しても大丈夫です</div>' : ''}
    </div>
    <div class="ml-actions">
      <button type="button" class="ghost-btn ml-small ml-edit" data-ml-edit="${esc(e.id)}">✏️ 編集</button>
      <button type="button" class="ghost-btn ml-small ml-del" data-ml-del="${esc(e.id)}">🗑 削除</button>
      <button type="button" class="ghost-btn ml-small" data-ml-only="${esc(e.id)}">これだけ調べる</button>
    </div>
  </div>`;
}

function renderListGroups() {
  if (!ML) return;
  const searching = !!ml.search.trim() || ml.filter !== 'all';
  // 編集中の行は絞り込みに関係なく表示したままにする
  const shown = ML.entries.filter(e => e.id === ml.editId || mlMatches(e));
  const keepNg = $('mlNg_e') ? $('mlNg_e').value : '';
  $('mlGroups').innerHTML = shown.length ? shown.map(mlEntryHtml).join('') : (searching && ML.entries.length
    ? `<div class="ml-empty"><div class="ml-empty-title">条件に合うブランドがありません</div>
        <div>検索の文字や「すべて / オンだけ / オフだけ」を変えてみてください。新しく登録するときは、上の<b>「ブランド」</b>に入れて<b>「＋ 追加」</b>を押してください。</div>
        <button type="button" class="ghost-btn ml-small" data-ml-goquick="1">↑ ブランドを登録する</button></div>`
    : `<div class="ml-empty"><div class="ml-empty-title">まだブランドが登録されていません</div>
        <div>上の<b>「ブランド」</b>に仕入れたいブランドを入れて、<b>「＋ 追加」</b>を押してください（Enterキーでも追加できます）。</div>
        <button type="button" class="ghost-btn ml-small" data-ml-goquick="1">↑ ブランドを登録する</button></div>`);
  if ($('mlNg_e')) $('mlNg_e').value = keepNg;
}

// ── マイブランドでリサーチ ───────────────────────────────
function mlTargetEntries() {
  if (!ML) return [];
  const t = ml.target || '';
  if (t.startsWith('id:')) return ML.entries.filter(e => e.id === t.slice(3));
  return ML.entries.filter(e => e.enabled);
}
function mlTargetLabel() {
  const t = ml.target || '';
  const n = mlTargetEntries().length;
  if (t.startsWith('id:')) { const e = mlTargetEntries()[0]; return e ? `「${e.name}」だけ` : '（削除されたブランド）'; }
  return `オンのブランドすべて（${n}件）`;
}
function mlEstimate() {
  const entries = mlTargetEntries();
  const enSet = new Set((ML && ML.en_brands) || []);
  let pairs = 0, q = 0;
  const src = Math.max(1, state.sources.size);
  let total = 0;
  entries.forEach(e => {
    const bq = (e.en_query || enSet.has(e.name)) ? 2 : 1;
    const per = Number(e.per_source) || Number(cond.per_source) || 20;
    const terms = (e.models || []).length + (e.model_numbers || []).length;
    let ep = 0, eq = 0;
    if (terms) { ep = terms; eq = bq * terms; }
    else (e.effective_genres || []).forEach(g => { ep += 1; eq += bq * mlGenreQueries(g); });
    pairs += ep; q += eq;
    total += eq * src * (0.1 + per / 120) + ep * 0.6 + eq * src * per * 0.35 / 60;
  });
  return { brands: entries.length, pairs, q, lo: Math.max(1, Math.round(total * 0.6)), hi: Math.max(2, Math.round(total * 1.5)) };
}
const mlFmtMin = m => (m >= 60 ? `${Math.floor(m / 60)}時間${m % 60 ? (m % 60) + '分' : ''}` : `${m}分`);

function renderListResearch() {
  if (!OPTIONS || !ML) return;
  const sel = $('mlTarget');
  const opts = [['', `オンのブランドすべて（${ML.entries.filter(e => e.enabled).length}件）`]];
  if (ml.target.startsWith('id:')) {
    const e = ML.entries.find(x => x.id === ml.target.slice(3));
    if (e) opts.push([ml.target, `「${e.name}」だけ`]);
  }
  if (!opts.some(([v]) => v === ml.target)) ml.target = '';
  sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}" ${v === ml.target ? 'selected' : ''}>${esc(l)}</option>`).join('');

  if (document.activeElement !== $('mlPriceMin')) $('mlPriceMin').value = free.price_min;
  if (document.activeElement !== $('mlPriceMax')) $('mlPriceMax').value = free.price_max;
  $('mlGenderSeg').innerHTML = FREE_GENDERS.map(g => `<button type="button" class="${free.gender === g ? 'on' : ''}" data-ml-gender="${g}">${g}</button>`).join('');
  if (document.activeElement !== $('mlMinProfit')) $('mlMinProfit').value = cond.min_profit;
  if (document.activeElement !== $('mlMinRoi')) $('mlMinRoi').value = cond.min_roi;
  $('mlPerSourceSeg').innerHTML = PER_SOURCE_PRESETS.map(([v, l]) =>
    `<button type="button" class="${Number(cond.per_source) === v ? 'on' : ''}" data-ml-per="${v}"><b>${l}</b><span>${v}件</span></button>`).join('');
  $('mlPhotoOcrLine').hidden = !OPTIONS.photo_ocr_available;
  $('mlPhotoOcr').checked = !!cond.photo_ocr;
  $('mlPhotoLocalLine').hidden = !OPTIONS.photo_local_installed;
  $('mlPhotoLocal').disabled = !OPTIONS.photo_local_available;
  $('mlPhotoLocal').checked = !!(OPTIONS.photo_local_available && cond.photo_local);
  $('mlPhotoCheckLine').hidden = !OPTIONS.photo_check_available;
  $('mlPhotoCheck').checked = !!cond.photo_check;
  $('mlSourceGrid').innerHTML = OPTIONS.sources.map(s => {
    const onS = state.sources.has(s.key);
    return `
    <button type="button" class="pick-card source-card ${onS ? 'on' : ''}" aria-pressed="${onS}" data-toggle="sources" data-value="${esc(s.key)}">
      <span class="check" aria-hidden="true"></span>
      <span class="src-icon" aria-hidden="true">${SOURCE_ICONS[s.key] || '🏬'}</span>
      <span class="src-text">
        <span class="src-name">${esc(s.label)}${s.auction ? ' <span class="auction-badge">オークション</span>' : ''}</span>
        <span class="src-desc">${esc(SOURCE_DESC[s.key] || '')}</span>
      </span>
    </button>`;
  }).join('');
  $('mlShoppingNew').checked = !!cond.yahoo_shopping_new;
  renderListFinal();
}

function listError() {
  const ents = mlTargetEntries();
  if (!ents.length) return ml.target ? 'そのブランドは見つかりません（削除されたかもしれません）' : 'オンのブランドがありません（ブランドのスイッチを「リサーチする」にしてください）';
  if (!ents.some(e => (e.effective_genres || []).length || (e.models || []).length || (e.model_numbers || []).length)) return '調べるジャンルがありません（ブランドの「✏️ 編集」でジャンルを選んでください）';
  if (!state.sources.size) return '仕入れ先を1つ以上選んでください';
  if (Number(free.price_max) > 0 && Number(free.price_min) > Number(free.price_max)) return '仕入れ値の範囲（下限 > 上限）を見直してください';
  return '';
}

function renderListFinal() {
  if (!OPTIONS || !ML || !$('mlSummary')) return;
  const e = mlEstimate();
  const srcs = OPTIONS.sources.filter(s => state.sources.has(s.key)).map(s => s.label);
  const rows = [
    ['ブランド', mlTargetLabel()],
    ['調べる組', e.pairs ? `${e.pairs}組（ブランド×ジャンル、型番・モデル名を入れたブランドは×型番・モデル名）` : ''],
    ['仕入れ値', `${num(free.price_min)}円〜${Number(free.price_max) > 0 ? num(free.price_max) + '円' : '上限なし'}（送料込み）${mlTargetEntries().some(x => x.price_min || x.price_max) ? '・金額を登録したブランドはその金額' : ''}`],
    ['性別', free.gender],
    ['仕入れ先', srcs.join('・')],
    ['利益', profitText()],
    ['取得件数', `${cond.per_source}件${mlTargetEntries().some(x => x.per_source) ? '・検索数を登録したブランドはその件数' : ''}`],
  ];
  $('mlSummary').innerHTML = rows.map(([k, v]) => `<div class="fs-row"><dt>${k}</dt><dd class="${v ? '' : 'missing'}">${esc(v || '未選択')}</dd></div>`).join('');
  const err = listError();
  $('mlFinalLine').innerHTML = err ? `<span class="warn">⚠ ${esc(err)}</span>`
    : `検索語 約<b>${e.q}</b>本 × 仕入れ先 <b>${state.sources.size}</b>か所・所要時間の目安 <b>約${mlFmtMin(e.lo)}〜${mlFmtMin(e.hi)}</b>（写真の確認の時間は別）`;
  const warn = $('mlWarn');
  if (!err && (e.brands > 10 || e.hi > 60)) {
    warn.hidden = false;
    warn.textContent = `⚠ ${e.brands}ブランド・${e.pairs}組を順番に調べるので、${mlFmtMin(e.lo)}〜${mlFmtMin(e.hi)}ほどかかりそうです。はじめはオンのブランドを減らすか、取得件数を「少なめ」にするのがおすすめです。途中で「中止」しても、そこまでの結果は見られます。`;
  } else warn.hidden = true;
  $('mlStartBtn').disabled = !!err || isRunning();
}

function collectListConditions() {
  const c = collectConditions();
  const t = ml.target || '';
  return { ...c, mode: 'list', categories: [], brands: [], models: [], model_numbers: [], free_numbers: '',
    list_group: '',
    list_ids: t.startsWith('id:') ? [t.slice(3)] : [],
    list_label: mlTargetLabel(),
    free_price_min: String(free.price_min), free_price_max: String(free.price_max), free_gender: free.gender };
}
async function mlStartResearch() {
  $('mlErrorLine').textContent = '';
  const err = listError();
  if (err) { $('mlErrorLine').textContent = err; return; }
  saveFree();
  await postResearch(collectListConditions(), 'mlErrorLine', 'mlStartBtn');
}
function listCondSummary(c) {
  const parts = [`マイブランド：${c.list_label || 'オンのブランド'}`];
  if (c.free_gender && c.free_gender !== '指定なし') parts.push(c.free_gender);
  parts.push(`${num(c.free_price_min)}〜${num(c.free_price_max)}円`);
  if (c.min_profit !== undefined && c.min_profit !== '') parts.push(`利益${num(c.min_profit)}円以上`);
  return parts.join(' / ');
}
function mlScrollResearch() {
  const el = $('mlResearch');
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function mlSetTarget(t) {
  ml.target = t;
  try { localStorage.setItem(ML_TARGET_KEY, ''); } catch (e) { /* なくても動く */ }
  renderListResearch();
}

// ── 編集（その行の中で。7項目） ──────────────────
const ML_EDIT_FIELDS = { mlEditName: 'name', mlEditNumbers: 'numbers', mlEditModels: 'models', mlEditMin: 'min', mlEditMax: 'max', mlEditPer: 'per' };
function mlKeepDraft() {
  Object.entries(ML_EDIT_FIELDS).forEach(([id, k]) => { if ($(id)) ml.editDraft[k] = $(id).value; });
}
function mlStartEdit(entry) {
  ml.editId = entry.id;
  ml.confirmDel = null;
  ml.editGenres = new Set(entry.genres || []);
  ml.editDraft = {
    name: entry.name, min: entry.price_min ? String(entry.price_min) : '', max: entry.price_max ? String(entry.price_max) : '',
    numbers: (entry.model_numbers || []).join('、'), models: (entry.models || []).join('、'), per: entry.per_source ? String(entry.per_source) : '',
  };
  ml.ngOpen.e = false;
  renderListGroups();
  const el = $('mlEditName');
  if (el) { el.focus(); el.select(); }
}
function mlCancelEdit() {
  const id = ml.editId;
  ml.editId = null;
  renderListGroups();
  const btn = id && document.querySelector(`[data-ml-edit="${CSS.escape(id)}"]`);
  if (btn) btn.focus();
}
let mlEditBusy = false;
async function mlSaveEdit() {
  if (!ml.editId || mlEditBusy) return;
  mlKeepDraft();
  const id = ml.editId;
  const d = ml.editDraft;
  const f = {
    name: d.name.trim(), model_numbers: String(d.numbers).trim(), models: String(d.models).trim(),
    price_min: String(d.min).trim(), price_max: String(d.max).trim(), per_source: d.per,
  };
  const name = f.name;
  const ferr = mlFieldsError(f);
  if (ferr) { $('mlEditErr').textContent = ferr; const el = !name ? $('mlEditName') : ferr.startsWith('下限') ? $('mlEditMin') : $('mlEditMax'); if (el) el.focus(); return; }
  mlEditBusy = true;
  const r = await mlApi('PUT', `/api/mylist/entry/${encodeURIComponent(id)}`, { ...f, genres: [...ml.editGenres] });
  mlEditBusy = false;
  if (!r.ok) { if ($('mlEditErr')) $('mlEditErr').textContent = r.error || '保存できませんでした'; return; }
  ml.editId = null;
  mlHighlight(id);
  renderList();
  mlFlash(`✓ 「${name}」を保存しました`);
  const btn = document.querySelector(`[data-ml-edit="${CSS.escape(id)}"]`);
  if (btn) btn.focus({ preventScroll: true });
}

// ── 操作 ─────────────────────────────────────────
function handleListClick(e) {
  if (!isListView()) return false;
  const t = e.target;
  let el;
  if ((el = t.closest('[data-ml-genre]'))) {
    const scope = el.dataset.mlGenre, g = el.dataset.g;
    const set = scope === 'q' ? ml.quickGenres : ml.editGenres;
    if (set.has(g)) set.delete(g); else set.add(g);
    if (scope === 'q') renderListQuick(); else { mlKeepDraft(); renderListGroups(); }
    return true;
  }
  if ((el = t.closest('[data-ml-ng-open]'))) {
    const scope = el.dataset.mlNgOpen;
    if (scope === 'e') mlKeepDraft();
    ml.ngOpen[scope] = true;
    if (scope === 'q') renderListQuick(); else renderListGroups();
    const inp = $(`mlNg_${scope}`);
    if (inp) inp.focus();
    return true;
  }
  if ((el = t.closest('[data-ml-ng-close]'))) {
    const scope = el.dataset.mlNgClose;
    if (scope === 'e') mlKeepDraft();
    ml.ngOpen[scope] = false;
    if (scope === 'q') renderListQuick(); else renderListGroups();
    return true;
  }
  if ((el = t.closest('[data-ml-ng-add]'))) { mlAddGenre(el.dataset.mlNgAdd); return true; }
  if (t.closest('[data-ml-goquick]')) {
    $('mlQuick').scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => $('mlQuickName').focus(), 300);
    return true;
  }
  if ((el = t.closest('[data-ml-filter]'))) { mlKeepDraft(); ml.filter = el.dataset.mlFilter; renderList(); return true; }
  if ((el = t.closest('[data-ml-all]'))) {
    const on = el.dataset.mlAll === 'on';
    mlKeepDraft();
    mlApi('POST', '/api/mylist/toggle', { group: '', enabled: on }).then(r => {
      renderList();
      mlFlash(r.ok ? `${r.changed}件を${on ? 'オン（リサーチする）' : 'オフ（しない）'}にしました` : (r.error || '保存できませんでした'));
    });
    return true;
  }
  if ((el = t.closest('[data-ml-edit]'))) {
    const entry = ML.entries.find(x => x.id === el.dataset.mlEdit);
    if (entry) mlStartEdit(entry);
    return true;
  }
  if (t.closest('[data-ml-edit-save]')) { mlSaveEdit(); return true; }
  if (t.closest('[data-ml-edit-cancel]')) { mlCancelEdit(); return true; }
  if ((el = t.closest('[data-ml-del]'))) { mlKeepDraft(); ml.confirmDel = el.dataset.mlDel; renderListGroups(); return true; }
  if (t.closest('[data-ml-del-no]')) { ml.confirmDel = null; renderListGroups(); return true; }
  if ((el = t.closest('[data-ml-del-yes]'))) {
    const id = el.dataset.mlDelYes;
    const entry = ML.entries.find(x => x.id === id);
    el.disabled = true;
    mlApi('DELETE', `/api/mylist/entry/${encodeURIComponent(id)}`).then(r => {
      ml.confirmDel = null;
      if (ml.editId === id) ml.editId = null;
      renderList();
      mlFlash(r.ok ? `「${entry ? entry.name : ''}」を削除しました` : (r.error || '削除できませんでした'));
    });
    return true;
  }
  if ((el = t.closest('[data-ml-only]'))) {
    mlSetTarget(`id:${el.dataset.mlOnly}`);
    mlScrollResearch();
    return true;
  }
  if ((el = t.closest('[data-ml-gender]'))) { free.gender = el.dataset.mlGender; saveFree(); renderListResearch(); return true; }
  if ((el = t.closest('[data-ml-per]'))) { cond.per_source = Number(el.dataset.mlPer); saveState(); renderListResearch(); return true; }
  return false;
}

function handleListChange(e) {
  if (!isListView()) return false;
  const t = e.target;
  if (t.matches && t.matches('[data-ml-toggle]')) {
    const id = t.dataset.mlToggle, on = t.checked;
    const entry = ML.entries.find(x => x.id === id);
    if (entry) entry.enabled = on;   // すぐ見た目に反映（保存はサーバー）
    mlKeepDraft();
    mlApi('POST', '/api/mylist/toggle', { ids: [id], enabled: on }).then(r => {
      if (!r.ok) mlFlash(r.error || '保存できませんでした');
      renderList();
    });
    return true;
  }
  const id = t.id;
  if (id === 'mlTarget') { mlSetTarget(t.value); return true; }
  if (id === 'mlPriceMin') { free.price_min = Math.max(0, Number(t.value) || 0); saveFree(); renderListFinal(); return true; }
  if (id === 'mlPriceMax') { free.price_max = Math.max(0, Number(t.value) || 0); saveFree(); renderListFinal(); return true; }
  if (id === 'mlMinProfit') { cond.min_profit = Math.max(0, Number(t.value) || 0); saveState(); renderListFinal(); return true; }
  if (id === 'mlMinRoi') { cond.min_roi = Math.max(0, Number(t.value) || 0); saveState(); renderListFinal(); return true; }
  if (id === 'mlPhotoOcr') { cond.photo_ocr = t.checked; saveState(); renderListFinal(); return true; }
  if (id === 'mlPhotoLocal') { cond.photo_local = t.checked; saveState(); renderListFinal(); return true; }
  if (id === 'mlPhotoCheck') { cond.photo_check = t.checked; saveState(); renderListFinal(); return true; }
  return false;
}

document.addEventListener('input', e => {
  if (!e.target) return;
  if (e.target.id === 'mlSearch') { mlKeepDraft(); ml.search = e.target.value; renderListGroups(); }
  else if (ML_EDIT_FIELDS[e.target.id]) mlKeepDraft();
});
document.addEventListener('keydown', e => {
  if (!e.target || e.isComposing) return;
  const id = e.target.id;
  if (e.key === 'Escape') {
    if (id === 'mlNg_q' || id === 'mlNg_e') { e.preventDefault(); const s = id.slice(-1); if (s === 'e') mlKeepDraft(); ml.ngOpen[s] = false; s === 'q' ? renderListQuick() : renderListGroups(); }
    else if (ml.editId && ML_EDIT_FIELDS[id]) { e.preventDefault(); mlCancelEdit(); }
    return;
  }
  if (e.key !== 'Enter' || e.keyCode === 229) return;   // 日本語入力の変換確定の Enter は使わない
  if (['mlQuickName', 'mlQuickNumbers', 'mlQuickModels', 'mlQuickMin', 'mlQuickMax'].includes(id)) { e.preventDefault(); mlQuickAdd(); }
  else if (ML_EDIT_FIELDS[id] && id !== 'mlEditPer') { e.preventDefault(); mlSaveEdit(); }
  else if (id === 'mlNg_q' || id === 'mlNg_e') { e.preventDefault(); mlAddGenre(id.slice(-1)); }
});

// 調べるブランドは毎回「オンのブランドすべて」から（昔保存したグループの指定は使わない）。リストは裏で先に読み込んでおく
try { localStorage.removeItem(ML_TARGET_KEY); } catch (e) { /* なくても動く */ }
ml.target = '';
mlLoad();
