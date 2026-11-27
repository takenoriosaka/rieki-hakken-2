// モデル登録（写真と相場を登録して、写真が同じ出品だけ探す）。保存はサーバー側の model_registry.json / model_photos/（/api/models*）
// research.js・my_list.js の後に読み込む（OPTIONS・state・cond・$・esc・num・yen・collectConditions・postResearch などを使う）

let MR = null;                 // /api/models の内容
const mr = {
  files: [],                   // これから登録する写真（File）
  picks: [],                   // メルカリのURLから取り込んだ写真 [{url, on}]（on のものを登録する）
  target: '',                  // '' = 「探す」のモデルすべて / 'id:xxxx' = そのモデルだけ
  editId: null, confirmDel: null, busy: false, msgTimer: null,
};

function isModelView() { return !!$('modelView') && !$('modelView').hidden; }
async function showModels() {
  showView('model');
  if (!MR) await mrLoad(false);
  mrRender();
  // 共有につないでいれば、開くたびに最新を取り込む（サーバー側で30秒に1回まで）
  if (MR && MR.share && MR.share.connected) { await mrLoad(true); mrRender(); }
}
async function mrLoad(sync) {
  try {
    MR = await (await fetch('/api/models' + (sync ? '?sync=1' : ''))).json();
    mr.shareWarn = MR.share_warn || '';
  } catch (e) { MR = null; }
}
function mrEntries() { return (MR && MR.entries) || []; }
function mrPhotoUrl(file) { return `/api/models/photo/${encodeURIComponent(file)}`; }

function mrMsg(text, ok) {
  const el = $('mrMsg');
  el.textContent = text;
  el.classList.toggle('warn', !ok);
  clearTimeout(mr.msgTimer);
  if (ok) mr.msgTimer = setTimeout(() => { el.textContent = ''; }, 5000);
}
async function mrApi(method, url, body, isForm) {
  try {
    const opt = { method };
    if (isForm) opt.body = body;
    else if (body) { opt.headers = { 'Content-Type': 'application/json' }; opt.body = JSON.stringify(body); }
    const data = await (await fetch(url, opt)).json();
    if (data.models) MR = data.models;
    if (data.models && !url.endsWith('/share')) { mr.shareWarn = data.warn || ''; mrRenderShare(); }
    return data;
  } catch (e) {
    return { ok: false, error: 'サーバーに接続できません（start.command で起動しているか確認してください）' };
  }
}

// ── 登録フォーム ─────────────────────────────────
function mrAddFiles(list) {
  const max = (MR && MR.max_photos) || 8;
  for (const f of list) {
    if (!f || !String(f.type).startsWith('image/')) continue;
    if (mr.files.length >= max) { mrMsg(`写真は1モデル${max}枚までです`, false); break; }
    mr.files.push(f);
  }
  mrRenderPreviews();
}
function mrRenderPreviews() {
  const box = $('mrPreviews');
  box.querySelectorAll('img').forEach(i => URL.revokeObjectURL(i.src));
  box.innerHTML = mr.files.map((f, i) =>
    `<div class="mr-thumb"><img src="${esc(URL.createObjectURL(f))}" alt=""><button type="button" class="mr-thumb-x" data-mr-unpick="${i}" title="この写真をやめる" aria-label="この写真をやめる">×</button></div>`).join('');
}
// メルカリの商品ページのURL → 写真を並べて、登録する写真を選ぶ（押すたびに 選ぶ/外す）
function mrRenderPicks() {
  $('mrPicks').innerHTML = mr.picks.map((p, i) =>
    `<button type="button" class="mr-thumb mr-pick ${p.on ? 'on' : ''}" data-mr-pick="${i}" title="${p.on ? '登録する写真（押すと外す）' : '押すと登録する写真にする'}" aria-pressed="${p.on}"><img src="${esc(p.url)}" alt="" referrerpolicy="no-referrer" loading="lazy"><span class="mr-pick-mark">✓</span></button>`).join('');
}
function mrUrlMsg(text, ok) { const el = $('mrUrlMsg'); el.textContent = text; el.classList.toggle('warn', !ok); }
async function mrFetchUrl() {
  const url = $('mrUrl').value.trim();
  if (!url) { mrUrlMsg('メルカリの商品ページのURLを貼ってください', false); return; }
  $('mrFetchBtn').disabled = true;
  mrUrlMsg('メルカリのページを読んでいます…（10秒ほど）', true);
  mrUrlMsg('メルカリのページを読んでいます…（タグの型番も読むので20秒ほど）', true);
  const d = await mrApi('POST', '/api/models/fetch', { url, brand: $('mrBrand').value.trim() });
  $('mrFetchBtn').disabled = false;
  if (!d.ok) { mrUrlMsg(d.error || '取り込めませんでした', false); return; }
  const it = d.item;
  mr.picks = it.images.map((u, i) => ({ url: u, on: i === 0 }));
  mrRenderPicks();
  let note = `写真 ${it.images.length}枚。登録する写真を押して選んでください（1枚目は選択済み）。`;
  // 売り切れの商品だけ、売れた値段を相場の欄に入れる（出品中の値段は売れた値段ではないので、知らせるだけ）
  if (it.price && it.sold && !$('mrPrice').value) {
    $('mrPrice').value = it.price;
    note += ` 相場の欄に、売れた値段 ${yen(it.price)} を入れました（直せます）。`;
  } else if (it.price) {
    note += ` ${it.sold ? '売れた値段' : '出品中の値段'}は ${yen(it.price)}。`;
  }
  if ((it.numbers || []).length && !$('mrNumbers').value.trim()) {
    $('mrNumbers').value = it.numbers.join(', ');
    $('mrNumbers').closest('details').open = true;
    note += ` 写真のタグ・商品名から型番 ${it.numbers.join('・')} を「型番」の欄に入れました（直せます）。`;
  }
  if (it.title) note += ` 商品名：${it.title}`;
  mrUrlMsg(note, true);
}

async function mrAdd() {
  if (mr.busy) return;
  const brand = $('mrBrand').value.trim(), name = $('mrName').value.trim(), price = $('mrPrice').value.trim();
  if (!brand) { mrMsg('ブランドを入れてください', false); $('mrBrand').focus(); return; }
  if (!name) { mrMsg('モデル名を入れてください', false); $('mrName').focus(); return; }
  if (!price) { mrMsg('相場を入れてください', false); $('mrPrice').focus(); return; }
  const picked = mr.picks.filter(p => p.on).map(p => p.url);
  if (!mr.files.length && !picked.length) { mrMsg('写真を1枚以上選んでください', false); return; }
  const fd = new FormData();
  fd.append('brand', brand); fd.append('name', name); fd.append('query', $('mrQuery').value.trim()); fd.append('price', price);
  fd.append('numbers', $('mrNumbers').value.trim()); fd.append('zipper', $('mrZipper').value);
  mr.files.forEach(f => fd.append('photos', f, f.name || 'photo'));
  picked.forEach(u => fd.append('photo_urls', u));
  mr.busy = true; $('mrAddBtn').disabled = true;
  mrMsg($('mrZipper').value === 'auto' ? '登録しています…（写真からファスナーの色・型番を読むので少しかかります）' : '登録しています…', true);
  const d = await mrApi('POST', '/api/models', fd, true);
  mr.busy = false; $('mrAddBtn').disabled = false;
  if (!d.ok) { mrMsg(d.error || '登録できませんでした', false); return; }
  mr.files = []; mr.picks = [];
  mrRenderPreviews(); mrRenderPicks();
  $('mrUrl').value = ''; mrUrlMsg('', true);
  $('mrName').value = ''; $('mrPrice').value = ''; $('mrNumbers').value = ''; $('mrZipper').value = 'auto';   // 同じブランド・同じ検索の言葉で続けて登録しやすいように、その2つは残す
  const ne = d.entry || {};
  const auto = [ne.zipper ? `ファスナー：${mrZipLabel(ne.zipper)}` : '', (ne.numbers || []).length ? `型番：${ne.numbers.join('・')}` : ''].filter(Boolean);
  mrMsg(`「${name}」を登録しました${auto.length ? `（${auto.join('／')}。違っていたら「編集」で直せます）` : ''}`, true);
  mrRender();
  $('mrName').focus();
}

// ── 一覧 ─────────────────────────────────────
function mrRowHtml(e) {
  const photos = e.photos.map(f =>
    `<div class="mr-thumb"><img src="${esc(mrPhotoUrl(f))}" alt="" loading="lazy">${e.photos.length > 1 ? `<button type="button" class="mr-thumb-x" data-mr-delphoto="${esc(e.id)}|${esc(f)}" title="この写真を消す" aria-label="この写真を消す">×</button>` : ''}</div>`).join('');
  let main;
  if (mr.editId === e.id) {
    main = `<div class="mr-edit">
        <input type="text" class="text-in" id="mrEditBrand" value="${esc(e.brand)}" placeholder="ブランド" aria-label="ブランド">
        <input type="text" class="text-in" id="mrEditName" value="${esc(e.name)}" placeholder="モデル名" aria-label="モデル名">
        <input type="text" class="text-in" id="mrEditQuery" value="${esc(e.query)}" placeholder="検索する言葉（空欄ならブランド名だけ）" aria-label="検索する言葉">
        <input type="number" class="text-in" id="mrEditPrice" value="${esc(e.price)}" min="500" step="500" placeholder="相場（円）" aria-label="相場（円）">
        <input type="text" class="text-in" id="mrEditNumbers" value="${esc((e.numbers || []).join(', '))}" placeholder="型番（任意・複数はカンマ区切り）" aria-label="型番">
        <select class="text-in" id="mrEditZipper" aria-label="ファスナーの色">
          ${[['', 'ファスナー：指定なし'], ['黒', 'ファスナー：黒'], ['金属', 'ファスナー：金属色']].map(([v, t]) => `<option value="${v}" ${(e.zipper || '') === v ? 'selected' : ''}>${t}</option>`).join('')}
        </select>
      </div>
      <div class="mr-row-btns">
        <button type="button" class="primary-btn" data-mr-save="${esc(e.id)}">保存</button>
        <button type="button" class="ghost-btn ml-small" data-mr-cancel>やめる</button>
        <button type="button" class="ghost-btn ml-small" data-mr-suggest="${esc(e.id)}" title="登録した写真から型番（タグの文字）とファスナーの色（Mac内AI）を読み取って欄に入れます。保存を押すまで変わりません">📷 写真から自動で入れる</button>
        <span class="hint warn" id="mrEditMsg"></span>
      </div>`;
  } else {
    const del = mr.confirmDel === e.id
      ? `<span class="hint warn">消しますか？</span><button type="button" class="ghost-btn ml-small" data-mr-del-yes="${esc(e.id)}">消す</button><button type="button" class="ghost-btn ml-small" data-mr-del-no>やめる</button>`
      : `<button type="button" class="ghost-btn ml-small" data-mr-del="${esc(e.id)}">🗑 削除</button>`;
    main = `<div class="mr-brand">${esc(e.brand)}</div>
      <div class="mr-title">${esc(e.name)}</div>
      <div class="mr-meta">相場 <b>${yen(e.price)}</b>　／　検索：「${esc([e.brand, e.query].filter(Boolean).join(' '))}」　／　写真 ${e.photos.length}枚</div>
      ${(e.numbers || []).length || e.zipper ? `<div class="mr-meta">見分けるポイント：${[(e.numbers || []).length ? `型番 <b>${esc(e.numbers.join('・'))}</b>` : '', e.zipper ? `ファスナー <b>${esc(mrZipLabel(e.zipper))}</b>${e.zipper_auto ? '（写真から自動）' : ''}` : ''].filter(Boolean).join('　／　')}</div>` : ''}
      <div class="mr-row-btns">
        <button type="button" class="ghost-btn ml-small" data-mr-edit="${esc(e.id)}">✏️ 編集</button>
        <button type="button" class="ghost-btn ml-small" data-mr-addphoto="${esc(e.id)}">📷 写真を足す</button>
        <button type="button" class="ghost-btn ml-small" data-mr-only="${esc(e.id)}">これだけ調べる</button>
        ${del}
      </div>`;
  }
  return `<div class="mr-row ${e.enabled ? 'is-on' : ''}" data-mr-row="${esc(e.id)}">
    <div class="mr-row-photos">${photos}</div>
    <div class="mr-row-main">${main}</div>
    <label class="mr-onoff"><input type="checkbox" data-mr-toggle="${esc(e.id)}" ${e.enabled ? 'checked' : ''}> <span>${e.enabled ? '探す' : '探さない'}</span></label>
  </div>`;
}
function mrZipLabel(z) { return z === '金属' ? '金属色' : (z || '指定なし'); }
function mrRender() {
  if (!MR) {
    $('mrList').innerHTML = '<div class="mr-empty">サーバーに接続できません（start.command で起動しているか確認してください）</div>';
    return;
  }
  $('mrUnavailable').hidden = !!MR.available;
  mrRenderShare();
  const es = mrEntries();
  const brands = [...new Set([...es.map(e => e.brand), ...(((OPTIONS && OPTIONS.free) || {}).brands || []).map(b => (b && b.name) || b)])].filter(b => typeof b === 'string');
  $('mrBrandList').innerHTML = brands.slice(0, 500).map(b => `<option value="${esc(b)}"></option>`).join('');
  $('mrCount').innerHTML = `登録 <b>${es.length}</b>件（探す <b>${es.filter(e => e.enabled).length}</b>件）`;
  $('mrList').innerHTML = es.length ? es.map(mrRowHtml).join('')
    : '<div class="mr-empty">まだ登録がありません。上の欄から、写真と相場を入れて登録してください。</div>';
  mrRenderResearch();
}

// ── リサーチ ────────────────────────────────────
function mrTargetEntries() {
  const es = mrEntries();
  if (mr.target.startsWith('id:')) return es.filter(e => e.id === mr.target.slice(3));
  return es.filter(e => e.enabled);
}
function mrRenderResearch() {
  const es = mrEntries();
  if (mr.target.startsWith('id:') && !es.some(e => e.id === mr.target.slice(3))) mr.target = '';
  $('mrTarget').innerHTML = `<option value="">「探す」にしたモデルすべて（${es.filter(e => e.enabled).length}件）</option>` +
    es.map(e => `<option value="id:${esc(e.id)}" ${mr.target === 'id:' + e.id ? 'selected' : ''}>${esc(e.brand)} ${esc(e.name)} だけ</option>`).join('');
  $('mrMinProfit').value = cond.min_profit;
  $('mrPerSourceSeg').innerHTML = PER_SOURCE_PRESETS.map(([n, label]) =>
    `<button type="button" class="${Number(cond.per_source) === n ? 'on' : ''}" data-mr-per="${n}">${esc(label)}（${n}件）</button>`).join('');
  $('mrSources').innerHTML = OPTIONS.sources.map(s =>
    `<label class="switch-line"><input type="checkbox" data-mr-source="${esc(s.key)}" ${state.sources.has(s.key) ? 'checked' : ''}> <span>${SOURCE_ICONS[s.key] || ''} ${esc(SOURCE_LABELS[s.key] || s.key)}</span></label>`).join('');
  mrRenderFinal();
}
function mrError() {
  if (MR && !MR.available) return 'この Mac では写真の照合が使えません';
  if (!mrEntries().length) return 'まずモデルを登録してください';
  if (!mrTargetEntries().length) return '「探す」になっているモデルがありません';
  if (!state.sources.size) return '仕入れ先を1つ以上選んでください';
  return '';
}
function mrRenderFinal() {
  const ts = mrTargetEntries();
  const groups = new Set(ts.map(e => `${e.brand}|${e.query}`)).size;
  const err = mrError();
  $('mrFinalLine').textContent = err || `モデル ${ts.length}件（検索 ${groups}組）× 仕入れ先 ${state.sources.size}か所。利益 ${num(cond.min_profit)}円以上で、写真が同じ出品だけを出します。`;
  $('mrStartBtn').disabled = isRunning() || !!err;
  $('mrStartBtn').textContent = isRunning() ? '⏳ リサーチ実行中です' : '🔍 リサーチ開始';
}
function collectModelConditions() {
  const c = collectConditions();
  const ts = mrTargetEntries();
  return { ...c, mode: 'model', categories: [], brands: [], models: [], model_numbers: [], free_numbers: '',
    model_ids: mr.target.startsWith('id:') ? [mr.target.slice(3)] : [],
    model_label: mr.target.startsWith('id:') && ts[0] ? `「${ts[0].name}」だけ` : `${ts.length}件` };
}
async function mrStartResearch() {
  $('mrErrorLine').textContent = '';
  const err = mrError();
  if (err) { $('mrErrorLine').textContent = err; return; }
  saveState();
  await postResearch(collectModelConditions(), 'mrErrorLine', 'mrStartBtn');
}
function modelCondSummary(c) {
  const parts = [`登録モデル：${c.model_label || '「探す」のモデル'}`];
  if (c.min_profit !== undefined && c.min_profit !== '') parts.push(`利益${num(c.min_profit)}円以上`);
  return parts.join(' / ');
}
function mrScrollResearch() { $('mrResearch').scrollIntoView({ behavior: 'smooth', block: 'start' }); }

// ── 操作 ─────────────────────────────────────
async function mrSaveEdit(id) {
  const d = await mrApi('PUT', `/api/models/${encodeURIComponent(id)}`, {
    brand: $('mrEditBrand').value, name: $('mrEditName').value, query: $('mrEditQuery').value, price: $('mrEditPrice').value,
    numbers: $('mrEditNumbers').value, zipper: $('mrEditZipper').value });
  if (!d.ok) { $('mrEditMsg').textContent = d.error || '保存できませんでした'; return; }
  mr.editId = null;
  mrRender();
}
async function mrSuggest(id, btn) {
  btn.disabled = true;
  $('mrEditMsg').classList.remove('warn');
  $('mrEditMsg').textContent = '写真を読み取っています…（1枚5秒ほど）';
  const d = await mrApi('POST', `/api/models/${encodeURIComponent(id)}/suggest`);
  btn.disabled = false;
  if (mr.editId !== id || !$('mrEditMsg')) return;
  if (!d.ok) { $('mrEditMsg').classList.add('warn'); $('mrEditMsg').textContent = d.error || '読み取れませんでした'; return; }
  const s = d.suggest || {}, done = [];
  if (s.zipper) { $('mrEditZipper').value = s.zipper; done.push(`ファスナー：${mrZipLabel(s.zipper)}`); }
  const have = $('mrEditNumbers').value.split(/[,、\s]+/).filter(Boolean);
  const add = (s.numbers || []).filter(n => !have.includes(n));
  if (add.length) { $('mrEditNumbers').value = [...have, ...add].join(', '); done.push(`型番：${add.join('・')}`); }
  $('mrEditMsg').textContent = (done.length ? `${done.join('／')} を入れました。よければ「保存」を押してください。` : '写真から読み取れたものはありませんでした。') + (s.note ? ` ${s.note}` : '');
}
let mrPhotoTarget = null;
const mrPhotoInput = Object.assign(document.createElement('input'), { type: 'file', accept: 'image/*', multiple: true, hidden: true });
document.body.appendChild(mrPhotoInput);
mrPhotoInput.addEventListener('change', async () => {
  const files = [...mrPhotoInput.files];
  mrPhotoInput.value = '';
  if (!files.length || !mrPhotoTarget) return;
  const fd = new FormData();
  files.forEach(f => fd.append('photos', f, f.name || 'photo'));
  const d = await mrApi('POST', `/api/models/${encodeURIComponent(mrPhotoTarget)}/photos`, fd, true);
  mrMsg(d.ok ? '写真を足しました' : (d.error || '写真を足せませんでした'), !!d.ok);
  mrRender();
});

$('modelView').addEventListener('click', async e => {
  const t = e.target.closest('button');
  if (!t) return;
  const ds = t.dataset;
  if (t.id === 'mrPick') { $('mrFiles').click(); return; }
  if (t.id === 'mrAddBtn') { mrAdd(); return; }
  if (t.id === 'mrFetchBtn') { mrFetchUrl(); return; }
  if (ds.mrPick !== undefined) { const p = mr.picks[Number(ds.mrPick)]; if (p) p.on = !p.on; mrRenderPicks(); return; }
  if (ds.mrUnpick !== undefined) { mr.files.splice(Number(ds.mrUnpick), 1); mrRenderPreviews(); return; }
  if (ds.mrAll) { await mrApi('POST', '/api/models/toggle', { enabled: ds.mrAll === 'on' }); mrRender(); return; }
  if (ds.mrEdit) { mr.editId = ds.mrEdit; mr.confirmDel = null; mrRender(); $('mrEditName').focus(); return; }
  if (ds.mrCancel !== undefined) { mr.editId = null; mrRender(); return; }
  if (ds.mrSave) { mrSaveEdit(ds.mrSave); return; }
  if (ds.mrSuggest) { mrSuggest(ds.mrSuggest, t); return; }
  if (ds.mrDel) { mr.confirmDel = ds.mrDel; mrRender(); return; }
  if (ds.mrDelNo !== undefined) { mr.confirmDel = null; mrRender(); return; }
  if (ds.mrDelYes) { mr.confirmDel = null; await mrApi('DELETE', `/api/models/${encodeURIComponent(ds.mrDelYes)}`); mrRender(); return; }
  if (ds.mrAddphoto) { mrPhotoTarget = ds.mrAddphoto; mrPhotoInput.click(); return; }
  if (ds.mrDelphoto) {
    const [id, file] = ds.mrDelphoto.split('|');
    const d = await mrApi('DELETE', `/api/models/${encodeURIComponent(id)}/photos/${encodeURIComponent(file)}`);
    if (!d.ok) mrMsg(d.error || '写真を消せませんでした', false);
    mrRender();
    return;
  }
  if (ds.mrOnly) { mr.target = 'id:' + ds.mrOnly; mrRenderResearch(); mrScrollResearch(); return; }
  if (ds.mrPer) { cond.per_source = Number(ds.mrPer); saveState(); mrRenderResearch(); return; }
});
$('modelView').addEventListener('change', async e => {
  const t = e.target, ds = t.dataset || {};
  if (t.id === 'mrFiles') { mrAddFiles([...t.files]); t.value = ''; return; }
  if (t.id === 'mrTarget') { mr.target = t.value; mrRenderFinal(); return; }
  if (t.id === 'mrMinProfit') { cond.min_profit = Math.max(0, Number(t.value) || 0); saveState(); mrRenderFinal(); return; }
  if (ds.mrToggle) { await mrApi('PUT', `/api/models/${encodeURIComponent(ds.mrToggle)}`, { enabled: t.checked }); mrRender(); return; }
  if (ds.mrSource) {
    if (t.checked) state.sources.add(ds.mrSource); else state.sources.delete(ds.mrSource);
    saveState(); mrRenderFinal();
  }
});
$('modelView').addEventListener('keydown', e => {
  if (e.key !== 'Enter' || e.isComposing) return;
  if (e.target.id === 'mrUrl') { e.preventDefault(); mrFetchUrl(); }
  else if (e.target.closest('#mrForm') && e.target.tagName === 'INPUT') { e.preventDefault(); mrAdd(); }
  else if (e.target.closest('.mr-edit') && mr.editId) { e.preventDefault(); mrSaveEdit(mr.editId); }
});
// 写真のドラッグ＆ドロップ・貼り付け（⌘V）
const mrDrop = $('mrDrop');
['dragenter', 'dragover'].forEach(n => mrDrop.addEventListener(n, e => { e.preventDefault(); mrDrop.classList.add('is-over'); }));
['dragleave', 'drop'].forEach(n => mrDrop.addEventListener(n, e => { e.preventDefault(); mrDrop.classList.remove('is-over'); }));
mrDrop.addEventListener('drop', e => mrAddFiles([...(e.dataTransfer ? e.dataTransfer.files : [])]));
document.addEventListener('paste', e => {
  if (!isModelView() || !e.clipboardData) return;
  const imgs = [...e.clipboardData.items].filter(i => i.kind === 'file' && i.type.startsWith('image/')).map(i => i.getAsFile()).filter(Boolean);
  if (imgs.length) { e.preventDefault(); mrAddFiles(imgs); }
});


// ── みんなで共有（登録モデルをネット上の置き場所と合わせる。model_share.py）────────
function mrShareTime(iso) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return '';
  const p = n => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function mrRenderShare() {
  const sh = MR && MR.share;
  if (!sh) return;
  const on = !!sh.connected;
  if (mr.shareOpen === undefined) mr.shareOpen = false;
  $('mrShareState').textContent = on
    ? `共有中${sh.last_sync ? '（最後に合わせた時刻 ' + mrShareTime(sh.last_sync) + '）' : ''}${sh.pending ? '・まだ送れていない変更 ' + sh.pending + '件' : ''}`
    : '共有していません（この Mac だけ）';
  $('mrShareState').classList.toggle('on', on);
  $('mrShareSync').hidden = !on;
  $('mrShareToggle').textContent = mr.shareOpen ? '閉じる' : (on ? '設定' : '共有につなぐ');
  $('mrShareBody').hidden = !mr.shareOpen;
  $('mrShareForm').hidden = on;
  $('mrShareOn').hidden = !on;
  $('mrShareHelp').textContent = on
    ? `置き場所：${sh.url}　ここで登録・編集・削除すると、同じ共有につないでいる全員の一覧に反映されます。「探す／探さない」は人ごとです。`
    : '共有のURLと合言葉を入れると、同じ共有につないでいる人と登録モデル（写真と相場）を一緒に使えます。この Mac に今ある登録は共有に足されます。';
  const warn = mr.shareWarn || sh.last_error || '';
  if (warn) mrShareMsg(warn, false);
  else if ($('mrShareMsg').classList.contains('warn')) mrShareMsg('', true);
}
function mrShareMsg(text, ok) { const el = $('mrShareMsg'); el.textContent = text; el.classList.toggle('warn', !ok); }
function mrShareStat(s) {
  const parts = [];
  if (s.added) parts.push(`${s.added}件を取り込み`);
  if (s.updated) parts.push(`${s.updated}件を更新`);
  if (s.removed) parts.push(`${s.removed}件を削除`);
  if (s.sent) parts.push(`${s.sent}件を送信`);
  return parts.length ? parts.join('・') + 'しました' : '変更はありませんでした（最新です）';
}
async function mrShareDo(body, busy) {
  const btns = ['mrShareSync', 'mrShareConnect'].map($);
  btns.forEach(b => { b.disabled = true; });
  mr.shareWarn = '';
  mrShareMsg(busy, true);
  const d = await mrApi('POST', '/api/models/share', body);
  btns.forEach(b => { b.disabled = false; });
  mrRender();
  if (!d.ok) { mrShareMsg(d.error || '共有の処理に失敗しました', false); return false; }
  mrShareMsg(body.action === 'disconnect' ? '共有をやめました（この Mac の登録は残っています）' : mrShareStat(d.stat || {}), true);
  return true;
}
$('mrShareToggle').addEventListener('click', () => { mr.shareOpen = !mr.shareOpen; mrRenderShare(); });
$('mrShareSync').addEventListener('click', () => mrShareDo({ action: 'sync' }, '共有と合わせています…'));
$('mrShareConnect').addEventListener('click', async () => {
  const url = $('mrShareUrl').value.trim();
  if (!url) { mrShareMsg('共有のURLを入れてください', false); $('mrShareUrl').focus(); return; }
  if (await mrShareDo({ action: 'connect', url, key: $('mrShareKey').value.trim() }, 'つないでいます…（写真が多いと少しかかります）')) {
    $('mrShareKey').value = '';
    mr.shareOpen = false;
    mrRenderShare();
  }
});
$('mrShareCopy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(MR.share.url); mrShareMsg('共有のURLをコピーしました（合言葉は別に伝えてください）', true); }
  catch (e) { mrShareMsg(MR.share.url, true); }
});
$('mrShareOff').addEventListener('click', () => { $('mrShareOffAsk').hidden = false; });
$('mrShareOffNo').addEventListener('click', () => { $('mrShareOffAsk').hidden = true; });
$('mrShareOffYes').addEventListener('click', async () => { $('mrShareOffAsk').hidden = true; await mrShareDo({ action: 'disconnect' }, '共有をやめています…'); });
