/* ==========================================================================
   Arabic Base — Application logic
   - Builds the vocabulary table from AR_WORDS
   - Manages filters, search, learning states, and UI overlays
   - Keeps the interface aligned with hsk-base behavior
   ========================================================================== */
/* OUR_ARABIC_v3 — Application */
(function(){'use strict';

// ── Storage keys ──────────────────────────────────────────────────────────────
const K = {
  learned:'arabic_learned', fam:'arabic_fam', mode:'arabic_mode', pal:'arabic_pal',
  prefs:'arabic_prefs', lang:'arabic_ui_lang', snaps:'arabic_snaps',
  cols:'arabic_cols', events:'arabic_events'
};

// ── State ─────────────────────────────────────────────────────────────────────
let learned = new Set();
let fam     = new Set();
let allRows  = [];  // {key, pos, root, level, group, tr, w}
let wMap     = new Map();
let snaps    = [];
let currentLang = 'ru';
let tashkeelOn  = true;
let studyDeck   = []; let studyIdx = 0; let studyKnown = 0;
let quizWords   = []; let quizIdx  = 0; let quizCorrect = 0;
let quizPending = null;
let filterTiers = new Set();
let filterPos   = '';
let filterLetter = '';
let filterSubpos = '';
let searchTimer = null;
let searchQuery = '';
let lastSearchLogged = '';
let lastSearchTs = 0;
let voices      = [];
let ttsRate     = 1;
let dragSrc     = null;
let confirmCb   = null;
let rootMap     = new Map();
let eventsLog   = [];

const $ = id => document.getElementById(id);
const $$ = sel => document.querySelectorAll(sel);
const body = document.body;

// ── Load State ────────────────────────────────────────────────────────────────
function loadState(){
  try{ learned = new Set(JSON.parse(localStorage.getItem(K.learned)||'[]')); }catch(e){}
  try{ fam     = new Set(JSON.parse(localStorage.getItem(K.fam)   ||'[]')); }catch(e){}
  try{ snaps   = JSON.parse(localStorage.getItem(K.snaps)||'[]');           }catch(e){}
  try{ eventsLog = JSON.parse(localStorage.getItem(K.events)||'[]');        }catch(e){}

  currentLang  = localStorage.getItem(K.lang)||'ru';
  tashkeelOn   = true;
  const mode   = localStorage.getItem(K.mode)||'dark';

  body.className = '';
  if(mode !== 'light') body.classList.add(mode);
  body.dataset.pal = localStorage.getItem(K.pal)||'rose';
  if(currentLang === 'en') body.classList.add('lang-en');

  try{
    const cols = JSON.parse(localStorage.getItem(K.cols)||'{}');
    ['num','word','root','trans','ex'].forEach(c=>{
      if(cols[c] === false) body.classList.add('hide-'+c);
    });
  }catch(e){}
}

function saveProgress(){
  localStorage.setItem(K.learned, JSON.stringify([...learned]));
  localStorage.setItem(K.fam,     JSON.stringify([...fam]));
}

// ── Tashkeel ──────────────────────────────────────────────────────────────────
const TASHKEEL_RE = /[\u064B-\u0652\u0670\u0640]/g;
function stripTashkeel(s){ return s.replace(TASHKEEL_RE, ''); }
function displayAr(s){ return tashkeelOn ? s : stripTashkeel(s); }

// ── Root normalizer (triliteral / quadriliteral display) ────────────────────
// Arabic roots are usually 3 consonants, sometimes 4. We keep 4 when present,
// otherwise fall back to a light heuristic for display consistency.
function normalizeRoot(root, word){
  const clean = stripTashkeel(root||'').replace(/[^\u0621-\u064A]/g,'');
  if(clean.length >= 3 && clean.length <= 4) return clean;
  let s = stripTashkeel(word||'').replace(/[^\u0621-\u064A]/g,'');
  // common prefixes
  s = s.replace(/^(?:ال|وال|فال|بال|كال|لل)/,'');
  // suffixes (pronouns / plural / feminine)
  s = s.replace(/(هما|هم|هن|كما|كم|كن|نا|ها|ه|ي|ك|ة|ات|ان|ون|ين|وا|تم|تن|ا)$/,'');
  if(s.length >= 3) return s.slice(0,3);
  return clean || s || '—';
}

// ── Learner event log ───────────────────────────────────────────────────────
function logEvent(type, payload){
  const evt = { ts: new Date().toISOString(), type, payload: payload||{} };
  eventsLog.push(evt);
  if(eventsLog.length > 2000) eventsLog.splice(0, eventsLog.length - 2000);
  localStorage.setItem(K.events, JSON.stringify(eventsLog));
}

function normalizeArToken(s){
  return stripTashkeel((s||'').trim())
    .replace(/[أإآٱ]/g,'ا')
    .replace(/ى/g,'ي')
    .replace(/ؤ/g,'و')
    .replace(/ئ/g,'ي')
    .replace(/ة/g,'ه')
    .replace(/\s+/g,'');
}
// Sub-division lexicons (normalized Arabic)
const SUBPOS_LISTS = {
  question: new Set(['من','ماذا','ما','متى','اين','كيف','لماذا','كم','اي','هل','ا']),
  demonstrative: new Set(['هذا','هذه','هذان','هذين','هؤلاء','ذلك','تلك','اولئك','هنا','هناك']),
  pronoun: new Set(['انا','انت','انتي','انتم','انتن','هو','هي','هما','هم','هن','نحن','اياي','اياك','اياكم','اياكن','اياه','اياها','اياهم','اياهن']),
  relative: new Set(['الذي','التي','الذين','اللذان','اللذين','اللتان','اللتين','اللواتي','اللاتي','من','ما']),
  preposition: new Set(['في','على','عن','الى','من','مع','بين','عند','قبل','بعد','منذ','حتى','خلال','فوق','تحت','حول','نحو','لدى','لكل','ب','ك','ل']),
  conjunction: new Set(['و','ف','ثم','او','ام','بل','لكن','لان','حتى','اذا','اذن']),
  negation: new Set(['لا','لم','لن','ما','ليس','غير','بدون']),
  number: new Set(['صفر','واحد','احد','اثنان','اثنين','ثلاثة','اربعة','خمسة','ستة','سبعة','ثمانية','تسعة','عشرة','مئة','مائة','الف','مليون']),
  time: new Set(['اليوم','امس','غدا','الان','حين','وقت','ساعة','دقيقة','شهر','سنة','عام','اسبوع','صباح','مساء','ليلة'])
};

// Arabic news channels (auto-generated from iptv-org)
const NEWS_CHANNELS = (window.ALL_CHANNELS || []).map(row => ({
  name: row[0],
  url: row[1],
  label: row[2]
}));
function getSubposList(word){
  const n = normalizeArToken(word);
  const matches = [];
  Object.keys(SUBPOS_LISTS).forEach(k=>{
    if(SUBPOS_LISTS[k].has(n)) matches.push(k);
  });
  return matches;
}
// Initial-letter extractor (for Arabic alphabet filter)
function getInitialLetter(word){
  let s = stripTashkeel((word||'').trim());
  // strip definite article & common attached prefixes
  s = s.replace(/^(?:ال|وال|فال|بال|كال|لل)/, '');
  s = s.replace(/^[^\u0621-\u064A]+/, '');
  if(!s) return '';
  let ch = s[0];
  if(/[أإآٱ]/.test(ch)) ch = 'ا';
  if(ch === 'ى' || ch === 'ئ') ch = 'ي';
  if(ch === 'ؤ') ch = 'و';
  if(ch === 'ة') ch = 'ه';
  return ch;
}
function esc(s){
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ============================================================================
// Build Table
// - Render POS sections
// - Create rows from AR_WORDS
// - Track row metadata for filters and stats
// ============================================================================
// ── Build Table ───────────────────────────────────────────────────────────────
function buildTable(){
  const tbl = $('vocab-table');
  const POS_ORDER = ['اسم','فعل','صفة','ظرف','ضمير','علم','عدد','حرف'];
  const POS_LABELS = {
    'اسم':   {ar:'اسْم',   en:'Nouns',         ru:'Существительные'},
    'فعل':   {ar:'فِعْل',  en:'Verbs',         ru:'Глаголы'},
    'صفة':   {ar:'صِفَة',  en:'Adjectives',    ru:'Прилагательные'},
    'ظرف':   {ar:'ظَرْف',  en:'Adverbs',       ru:'Наречия'},
    'ضمير':  {ar:'ضَمِير', en:'Pronouns',      ru:'Местоимения'},
    'علم':   {ar:'عَلَم',  en:'Proper Nouns',  ru:'Имена собственные'},
    'عدد':   {ar:'عَدَد',  en:'Numbers',       ru:'Числа'},
    'حرف':   {ar:'حَرْف',  en:'Particles',     ru:'Частицы'}
  };

  const mapPos = (p)=>{
    p=(p||'').trim();
    if(!p) return 'اسم';
    if(p.indexOf('فعل')!==-1) return 'فعل';
    if(p.indexOf('صفة')!==-1) return 'صفة';
    if(p.indexOf('ظرف')!==-1) return 'ظرف';
    if(p.indexOf('ضمير')!==-1) return 'ضمير';
    if(p.indexOf('علم')!==-1) return 'علم';
    if(p.indexOf('عدد')!==-1) return 'عدد';
    if(p.indexOf('حرف')!==-1 || p.indexOf('أداة')!==-1) return 'حرف';
    return 'اسم';
  };

  function sortByRoot(words){
    return [...words].sort((a,b)=>{
      const ra = normalizeRoot(a.r, a.w);
      const rb = normalizeRoot(b.r, b.w);
      if(ra!==rb) return ra.localeCompare(rb,'ar');
      return (a.w||'').localeCompare(b.w||'','ar');
    });
  }

  let rowNum = 0;
  POS_ORDER.forEach(pos=>{
    const group = sortByRoot(AR_WORDS.filter(w=>mapPos(w.pos)===pos));
    if(!group.length) return;
    const lbl = POS_LABELS[pos] || {ar:pos, en:pos, ru:pos};
    const groupKey = 'pos:'+pos;

    const hdrRow = document.createElement('tr');
    hdrRow.className = 'pos-hdr-row';
    hdrRow.dataset.group = groupKey;
    const hdrTd = document.createElement('td');
    hdrTd.colSpan = 7;
    hdrTd.innerHTML = `<div class="grp-hdr" data-group="${groupKey}">
      <div class="grp-hdr-left">
        <span class="gh-arrow">&#9660;</span>
        <span class="gh-ar">${lbl.ar}</span>
        <span class="gh-label">&nbsp;&mdash; <span class="ui-ru">${lbl.ru}</span><span class="ui-en">${lbl.en}</span></span>
      </div>
      <span class="gh-count">${group.length} <span class="ui-ru">слов</span><span class="ui-en">words</span></span>
    </div>`;
    hdrRow.appendChild(hdrTd);
    tbl.appendChild(hdrRow);

    group.forEach(w=>{
      rowNum++;
      const tr = buildWordRow(w, rowNum, groupKey);
      tbl.appendChild(tr);
      const root = normalizeRoot(w.r, w.w);
      allRows.push({key:w.w, pos:mapPos(w.pos), root, level:w.level||1, tier:w.tier||w.level||1, group:groupKey, tr, w});
      wMap.set(w.w, tr);
    });
  });

  buildSpecialSection('learned');
  buildSpecialSection('fam');
  buildRootMap();
}

function buildWordRow(w, num, groupKey=''){
  const key = w.w;
  const root = normalizeRoot(w.r, w.w);
  const mapPos = (p)=>{
    p=(p||'').trim();
    if(!p) return 'اسم';
    if(p.indexOf('فعل')!==-1) return 'فعل';
    if(p.indexOf('حرف')!==-1 || p.indexOf('أداة')!==-1) return 'حرف';
    return 'اسم';
  };
  const pickRu = (w)=> (w.ru && w.ru.trim()) ? w.ru : (w.en||'');
  const pickXr = (w)=> (w.xr && w.xr.trim()) ? w.xr : (w.xe||'');
  const tr = document.createElement('tr');
  tr.className = 'word-row';
  tr.dataset.key   = key;
  tr.dataset.group = groupKey || 'pos:'+(mapPos(w.pos));
  tr.dataset.root  = root  || '—';
  tr.dataset.level = w.level || 1;
  tr.dataset.pos   = mapPos(w.pos);
  tr.dataset.subpos = getSubposList(key).join(',');
  tr.dataset.letter = getInitialLetter(key);
  tr.dataset.en    = (w.en||'').toLowerCase();
  tr.dataset.ru    = (w.ru||'').toLowerCase();
  tr.draggable     = true;

  if(learned.has(key)) tr.classList.add('learned');
  else if(fam.has(key)) tr.classList.add('familiar');

  const lv = w.level||1;
  const arWord = displayAr(key);
  const arEx   = displayAr(w.xa||'');

  const hasRoot = root && root !== '—';
  tr.innerHTML = `
<td data-col="cb"><input type="checkbox" class="learn-cb"${learned.has(key)?' checked':''}></td>
<td data-col="fam"><input type="checkbox" class="fam-cb"${fam.has(key)?' checked':''}></td>
<td data-col="num" class="rownum">${num}</td>
<td data-col="word" class="wordcell">
  <div class="wc-inner">
    <div class="wc-text">
      <span class="ar">${esc(arWord)}</span>
      <span class="wc-root">${esc(root||'')}</span>
      ${w.pl && w.pl !== '—' ? '<span class="wc-plural">جمع: '+esc(w.pl)+'</span>' : ''}
    </div>
    <div style="display:flex;flex-direction:column;align-items:center;gap:3px">
      <span class="lv-badge lv-${lv}">${lv}</span>
      <button class="tts-btn" data-ar="${esc(key)}" title="Speak">&#9654;</button>
    </div>
  </div>
</td>
<td data-col="root" class="root-cell">
  <button class="root-btn" data-root="${esc(root||'')}" ${hasRoot?'':'disabled'}>&#x25C7;</button>
  <span class="root-text">${esc(hasRoot?root:'—')}</span>
</td>
<td data-col="trans" class="trans-cell">
  <span class="trans-en">${esc(w.en||'')}</span>
  <span class="trans-ru">${esc(pickRu(w))}</span>
</td>
<td data-col="ex" class="ex-td">
  <div class="ex-inner">
    <div class="ex-text">
      <div class="ex-ar-line">
        <button class="tts-btn" data-ar="${esc(w.xa||'')}" title="Play">&#9654;</button>
        <span class="ex-ar">${esc(arEx)}</span>
      </div>
      <span class="ex-tr ex-en">${esc(w.xe||'')}</span>
      <span class="ex-tr ex-ru">${esc(pickXr(w))}</span>
    </div>
  </div>
</td>`;
  return tr;
}

function buildSpecialSection(type){
  const tbl = $('vocab-table');
  const tr = document.createElement('tr');
  tr.id = 'sec-'+type+'-hdr';
  tr.className = 'sec-title-row';
  tr.style.display = 'none';
  const td = document.createElement('td');
  td.colSpan = 7;
  td.innerHTML = type === 'learned'
    ? '<span class="ui-ru">&#x2713; Изучено</span><span class="ui-en">&#x2713; Learned</span>'
    : '<span class="ui-ru">&#x2605; Знакомые</span><span class="ui-en">&#x2605; Familiar</span>';
  tr.appendChild(td);
  tbl.appendChild(tr);
}

// ============================================================================
// Root-family expander
// - Builds a root → word list index
// - Renders root family blocks grouped by POS (ism/fi'l/harf)
// ============================================================================
function buildRootMap(){
  rootMap = new Map();
  allRows.forEach(r=>{
    const root = (r.root||'').trim();
    if(!root || root === '—') return;
    if(!rootMap.has(root)) rootMap.set(root, []);
    rootMap.get(root).push(r);
  });
}

function closeRootFamilies(){
  $$('.root-family-row').forEach(r=>r.remove());
  $$('.word-row.root-open').forEach(r=>r.classList.remove('root-open'));
}

function renderRootFamily(root, rows, currentKey){
  const byPos = { 'فعل':[], 'اسم':[], 'حرف':[] };
  rows.forEach(r=>{
    if(r.tr.style.display === 'none') return;
    (byPos[r.pos] || byPos['اسم']).push(r);
  });
  const order = [
    {pos:'فعل', cls:'verb', label:{ru:'Глаголы', en:'Verbs'}},
    {pos:'اسم', cls:'noun', label:{ru:'Существительные', en:'Nouns'}},
    {pos:'حرف', cls:'part', label:{ru:'Частицы', en:'Particles'}},
  ];
  const fmtItem = (r)=>{
    const w = r.w;
    const trText = currentLang==='ru' ? (w.ru||w.en||'') : (w.en||w.ru||'');
    const isCurrent = w.w === currentKey;
    return `<button class="rf-item${isCurrent?' current':''}" data-word="${esc(w.w)}">
      <span class="rf-ar">${esc(displayAr(w.w))}</span>
      <span class="rf-tr">${esc(trText)}</span>
    </button>`;
  };

  const groupsHtml = order.map(g=>{
    const list = byPos[g.pos] || [];
    if(!list.length) return '';
    return `<div class="rf-group">
      <div class="rf-group-title">
        <span class="rf-pos ${g.cls}">${g.pos}</span>
        <span class="ui-ru">${g.label.ru}</span><span class="ui-en">${g.label.en}</span>
        <span style="margin-left:auto;color:var(--text3)">${list.length}</span>
      </div>
      <div class="rf-items">${list.map(fmtItem).join('')}</div>
    </div>`;
  }).join('');

  return `<div class="root-family">
    <div class="rf-title">
      <span class="ui-ru">Корневая семья:</span><span class="ui-en">Root family:</span>
      <b style="margin-left:6px">${esc(root)}</b>
    </div>
    <div class="rf-groups">${groupsHtml || '<span style=\"color:var(--text3);font-size:12px\">No visible words in this root.</span>'}</div>
  </div>`;
}

function toggleRootFamily(tr){
  const root = tr.dataset.root || '';
  if(!root || root === '—') return;
  const next = tr.nextElementSibling;
  if(next && next.classList.contains('root-family-row') && next.dataset.root === root){
    next.remove();
    tr.classList.remove('root-open');
    return;
  }
  // Close any open row right after this word
  if(next && next.classList.contains('root-family-row')) next.remove();

  const rows = rootMap.get(root) || [];
  if(!rows.length) return;
  const rfRow = document.createElement('tr');
  rfRow.className = 'root-family-row';
  rfRow.dataset.root = root;
  const td = document.createElement('td');
  td.colSpan = 7;
  td.innerHTML = renderRootFamily(root, rows, tr.dataset.key);
  rfRow.appendChild(td);
  tr.parentNode.insertBefore(rfRow, tr.nextSibling);
  tr.classList.add('root-open');
}

// ============================================================================
// Stats
// - Learned / familiar counts
// - Level progress bars
// ============================================================================
// ── Stats ─────────────────────────────────────────────────────────────────────
function updateStats(){
  const setNum = (id, v) => { const el=$(id); if(el) el.textContent=v; };
  setNum('stat-total',   allRows.length);
  setNum('stat-learned', learned.size);
  setNum('stat-fam',     fam.size);
  [1,2,3,4,5,6,7].forEach(lv=>{
    const lvRows = allRows.filter(r=>r.tier===lv);
    const lvLearned = lvRows.filter(r=>learned.has(r.key)).length;
    const pct = lvRows.length ? Math.round(lvLearned/lvRows.length*100) : 0;
    const fill = $('lvl-fill-'+lv);
    const pctEl = $('lvl-pct-'+lv);
    if(fill) fill.style.width = pct+'%';
    if(pctEl) pctEl.textContent = pct+'%';
  });
  updatePosSummary();
  updateTierSummary();
}

// ── Tier summary counts (levels 1–7) ────────────────────────────────────────
function updateTierSummary(){
  const counts = {1:0,2:0,3:0,4:0,5:0,6:0,7:0};
  allRows.forEach(r=>{
    if(r.tr.style.display==='none') return;
    const t = r.tier || r.level || 1;
    if(counts[t] !== undefined) counts[t] += 1;
  });
  Object.keys(counts).forEach(k=>{
    const el = document.querySelector(`.tier-chip[data-tier="${k}"] .tc-count`);
    if(el) el.textContent = counts[k];
  });
}

// ── POS summary counts ───────────────────────────────────────────────────────
function updatePosSummary(){
  const counts = { '':0, 'اسم':0, 'فعل':0, 'صفة':0, 'ظرف':0, 'ضمير':0, 'علم':0, 'عدد':0, 'حرف':0 };
  allRows.forEach(r=>{
    if(r.tr.style.display==='none') return;
    counts['']++;
    if(counts[r.pos] !== undefined) counts[r.pos]++;
  });
  const set = (id,val)=>{ const el=$(id); if(el) el.textContent=val; };
  set('pos-count-all', counts['']);
  set('pos-count-noun', counts['اسم']);
  set('pos-count-verb', counts['فعل']);
  set('pos-count-adj', counts['صفة']);
  set('pos-count-adv', counts['ظرف']);
  set('pos-count-pron', counts['ضمير']);
  set('pos-count-prop', counts['علم']);
  set('pos-count-num', counts['عدد']);
  set('pos-count-part', counts['حرف']);
  updateSubposSummary();
}

// ── Sub-division counts (question words, pronouns, etc.) ────────────────────
function updateSubposSummary(){
  const base = {
    all:0, question:0, demonstrative:0, pronoun:0, relative:0,
    preposition:0, conjunction:0, negation:0, number:0, time:0
  };
  allRows.forEach(r=>{
    if(r.tr.style.display==='none') return;
    base.all++;
    const sp = (r.tr.dataset.subpos||'').split(',').filter(Boolean);
    sp.forEach(k=>{ if(base[k] !== undefined) base[k]++; });
  });
  const set = (id,val)=>{ const el=$(id); if(el) el.textContent=val; };
  set('subpos-count-all', base.all);
  set('subpos-count-question', base.question);
  set('subpos-count-demonstrative', base.demonstrative);
  set('subpos-count-pronoun', base.pronoun);
  set('subpos-count-relative', base.relative);
  set('subpos-count-preposition', base.preposition);
  set('subpos-count-conjunction', base.conjunction);
  set('subpos-count-negation', base.negation);
  set('subpos-count-number', base.number);
  set('subpos-count-time', base.time);
}

// ── Checkbox Logic ────────────────────────────────────────────────────────────
function onLearned(tr, cb){
  const key = tr.dataset.key;
  if(cb.checked){
    learned.add(key); fam.delete(key);
    tr.classList.add('learned'); tr.classList.remove('familiar');
    const fc = tr.querySelector('.fam-cb'); if(fc) fc.checked = false;
    moveToSec(tr,'learned');
    logEvent('learned', { word: key });
  } else {
    learned.delete(key); tr.classList.remove('learned');
    returnToPos(tr);
    logEvent('unlearned', { word: key });
  }
  saveProgress(); updateStats(); updateSecHdrs();
}

function onFam(tr, cb){
  const key = tr.dataset.key;
  if(cb.checked){
    fam.add(key); learned.delete(key);
    tr.classList.add('familiar'); tr.classList.remove('learned');
    const lc = tr.querySelector('.learn-cb'); if(lc) lc.checked = false;
    moveToSec(tr,'fam');
    logEvent('familiar', { word: key });
  } else {
    fam.delete(key); tr.classList.remove('familiar');
    returnToPos(tr);
    logEvent('unfamiliar', { word: key });
  }
  saveProgress(); updateStats(); updateSecHdrs();
}

function moveToSec(tr, type){
  const hdr = $('sec-'+type+'-hdr');
  if(!hdr) return;
  hdr.parentNode.insertBefore(tr, hdr.nextSibling);
}

function returnToPos(tr){
  // Return word row to its original section (special group or POS)
  const groupKey = tr.dataset.group || ('pos:' + (tr.dataset.pos||''));
  const rows = [...$$('tr.word-row')].filter(r=>r.dataset.group===groupKey && r!==tr);
  const tbl = $('vocab-table');
  if(rows.length){
    rows[rows.length-1].parentNode.insertBefore(tr, rows[rows.length-1].nextSibling);
  } else {
    const hdr = [...$$('tr.pos-hdr-row')].find(h=>h.dataset.group===groupKey);
    if(hdr) hdr.parentNode.insertBefore(tr, hdr.nextSibling);
    else tbl.appendChild(tr);
  }
}

function updateSecHdrs(){
  const lh = $('sec-learned-hdr'); if(lh) lh.style.display = learned.size ? '' : 'none';
  const fh = $('sec-fam-hdr');     if(fh) fh.style.display = fam.size     ? '' : 'none';
}

// ── Renum ─────────────────────────────────────────────────────────────────────
function renum(){
  let n = 0;
  $$('tr.word-row').forEach(tr=>{
    if(tr.style.display === 'none') return;
    n++;
    const c = tr.querySelector('[data-col=num]'); if(c) c.textContent = n;
  });
}

// ── Collapse / Expand ─────────────────────────────────────────────────────────
function togglePosGroup(ghDiv){
  const group = ghDiv.dataset.group || ('pos:' + (ghDiv.dataset.pos||''));
  const collapsed = ghDiv.classList.toggle('collapsed');
  allRows.filter(r=>r.group===group).forEach(r=>{
    r.tr.style.display = collapsed ? 'none' : '';
  });
}

// ============================================================================
// Search routine
// - Normalizes Arabic input (tashkeel-insensitive)
// - Matches AR/EN/RU fields and highlights hits
// ============================================================================
function doSearch(q){
  q = q.trim().toLowerCase();
  searchQuery = q;
  const qNorm = normalizeArToken(q);
  $$('tr.word-row').forEach(tr=>{
    clearHL(tr);
    if(!q){ tr.dataset.match = '1'; return; }
    const ar = normalizeArToken(tr.dataset.key||'');
    const ru = (tr.dataset.ru||'').toLowerCase();
    const en = (tr.dataset.en||'').toLowerCase();
    const hit = ar.includes(qNorm) || ru.includes(q) || en.includes(q);
    tr.dataset.match = hit ? '1' : '0';
    if(hit) applyHL(tr, q);
  });
  applyFilters();
  const now = Date.now();
  if(q && q !== lastSearchLogged && (now - lastSearchTs) > 800){
    logEvent('search', { q });
    lastSearchLogged = q;
    lastSearchTs = now;
  }
}

function applyHL(tr, q){
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi');
  tr.querySelectorAll('.ar,.trans-ru,.trans-en').forEach(el=>{
    if(el.textContent.toLowerCase().includes(q))
      el.innerHTML = el.textContent.replace(re, m=>`<mark>${m}</mark>`);
  });
}
function clearHL(tr){
  tr.querySelectorAll('mark').forEach(m=>{ m.outerHTML = m.textContent; });
}

function updatePosVis(){
  $$('.pos-hdr-row').forEach(hdr=>{
    const group = hdr.dataset.group || ('pos:' + (hdr.dataset.pos||''));
    const visible = allRows.some(r=>r.group===group && r.tr.style.display!=='none');
    hdr.style.display = visible ? '' : 'none';
  });
}

// ============================================================================
// Filter pipeline
// - Applies level (tier), POS, and search visibility rules
// - Triggers renumbering and stats refresh
// ============================================================================
function applyFilters(){
  // close root expansions when filters change (keeps layout consistent)
  closeRootFamilies();
  allRows.forEach(({tr, tier, pos})=>{
    let show = true;
    if(filterTiers.size && !filterTiers.has(tier)) show = false;
    if(filterPos && pos !== filterPos) show = false;
    if(filterSubpos){
      const sp = tr.dataset.subpos || '';
      if(!sp.split(',').includes(filterSubpos)) show = false;
    }
    if(filterLetter && tr.dataset.letter !== filterLetter) show = false;
    if(tr.dataset.match === '0') show = false;
    tr.style.display = show ? '' : 'none';
  });
  updatePosVis();
  renum();
  updateStats();
}

// ── TTS ───────────────────────────────────────────────────────────────────────
function initVoices(){ voices = window.speechSynthesis.getVoices(); }
function getArVoice(){
  return voices.find(v=>/ar[-_]SA/i.test(v.lang))
      || voices.find(v=>/^ar/i.test(v.lang))
      || null;
}
// ============================================================================
// TTS playback helper
// - Uses SpeechSynthesis with Arabic voice if available
// ============================================================================
function speak(text, btn){
  if(!text) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'ar-SA';
  const v = getArVoice(); if(v) u.voice = v;
  u.rate = ttsRate;
  if(btn){ btn.classList.add('on'); u.onend = ()=>btn.classList.remove('on'); }
  window.speechSynthesis.speak(u);
}

// ── Theme / Palette / Language ────────────────────────────────────────────────
function setMode(m){
  body.classList.remove('dark','sepia');
  if(m!=='light') body.classList.add(m);
  localStorage.setItem(K.mode, m);
  $$('[data-mode]').forEach(b=>b.classList.toggle('active', b.dataset.mode===m));
  logEvent('theme', { mode: m });
}

function setPalette(p){
  body.dataset.pal = p||'rose';
  localStorage.setItem(K.pal, p||'rose');
  $$('.pal-btn').forEach(b=>b.classList.toggle('active', b.dataset.pal===p));
  logEvent('palette', { palette: p||'rose' });
}

function setLang(l){
  currentLang = l;
  localStorage.setItem(K.lang, l);
  body.classList.toggle('lang-en', l==='en');
  $$('[data-lang]').forEach(b=>b.classList.toggle('active', b.dataset.lang===l));
  closeRootFamilies();
  logEvent('lang', { lang: l });
}

// ── Tashkeel ──────────────────────────────────────────────────────────────────
function setTashkeel(on){
  tashkeelOn = on;
  localStorage.setItem(K.tashkeel, on?'1':'0');
  body.classList.toggle('no-tashkeel', !on);
  // Refresh displayed Arabic text
  $$('tr.word-row').forEach(tr=>{
    const key = tr.dataset.key;
    const wData = AR_WORDS.find(w=>w.w===key);
    if(!wData) return;
    const arEl = tr.querySelector('.ar');
    if(arEl) arEl.textContent = displayAr(wData.w);
    const exEl = tr.querySelector('.ex-ar');
    if(exEl) exEl.textContent = displayAr(wData.xa||'');
  });
  const btn = $('tashkeel-btn');
  if(btn) btn.classList.toggle('active', on);
  closeRootFamilies();
  logEvent('tashkeel', { enabled: !!on });
}

// ── Font Prefs ────────────────────────────────────────────────────────────────
function applyFontPrefs(){
  const p = JSON.parse(localStorage.getItem(K.prefs)||'{}');
  let css = '';
  if(p.fontAr)  css += `.ar,.ex-ar,.root-cell,.wc-root{font-family:'${p.fontAr}',serif!important;}`;
  if(p.sizeAr)  css += `:root{--sz-ar:${p.sizeAr}px;--sz-ar-ex:${Math.max(13,p.sizeAr-8)}px;}`;
  if(p.fontRu)  css += `.trans-ru,.trans-en,.ex-tr{font-family:'${p.fontRu}',sans-serif!important;}`;
  if(p.sizeRu)  css += `.trans-ru,.trans-en,.ex-tr{font-size:${p.sizeRu}px!important;}`;
  $('dyn-font').textContent = css;
  // Sync sliders
  const sync = (id, val, dispId) => {
    const el = $(id); if(el&&val){ el.value=val; }
    const dsp=$(dispId); if(dsp&&val) dsp.textContent=val;
  };
  sync('fp-sz-ar', p.sizeAr, 'fp-sz-ar-val');
  sync('fp-sz-ru', p.sizeRu, 'fp-sz-ru-val');
  if(p.fontAr){ const el=$('fp-font-ar'); if(el) el.value=p.fontAr; }
  if(p.fontRu){ const el=$('fp-font-ru'); if(el) el.value=p.fontRu; }
}

function saveFontPrefs(){
  const p = {
    fontAr: ($('fp-font-ar')||{}).value||'',
    sizeAr: parseInt(($('fp-sz-ar')||{}).value)||26,
    fontRu: ($('fp-font-ru')||{}).value||'',
    sizeRu: parseInt(($('fp-sz-ru')||{}).value)||13,
  };
  localStorage.setItem(K.prefs, JSON.stringify(p));
  applyFontPrefs();
}

// ── Column Visibility ─────────────────────────────────────────────────────────
function toggleCol(col){
  body.classList.toggle('hide-'+col);
  const btn = $('col-btn-'+col);
  if(btn) btn.classList.toggle('active', body.classList.contains('hide-'+col));
  saveColState();
}
function saveColState(){
  const cols = {};
  ['num','word','root','trans','ex'].forEach(c=>{ cols[c] = !body.classList.contains('hide-'+c); });
  localStorage.setItem(K.cols, JSON.stringify(cols));
}

// ── Snapshots ─────────────────────────────────────────────────────────────────
function captureSnap(){
  snaps.unshift({ ts:new Date().toLocaleString(), learned:[...learned], fam:[...fam] });
  if(snaps.length > 20) snaps.length = 20;
  localStorage.setItem(K.snaps, JSON.stringify(snaps));
  renderSnaps();
}

function renderSnaps(){
  const list = $('snap-list');
  if(!list) return;
  if(!snaps.length){
    list.innerHTML='<div style="color:var(--text3);font-size:11px;text-align:center;padding:8px">No snapshots</div>';
    return;
  }
  list.innerHTML = snaps.map((s,i)=>`
    <div class="snap-item">
      <span class="snap-ts">${esc(s.ts)}</span>
      <span class="snap-info">${s.learned.length}L/${s.fam.length}F</span>
      <button class="snap-sbtn" data-restore="${i}"><span class="ui-ru">Восст.</span><span class="ui-en">Restore</span></button>
      <button class="snap-sbtn del" data-del="${i}">&#x2715;</button>
    </div>`).join('');
}

function restoreSnap(i){
  const s = snaps[i]; if(!s) return;
  confirm2('Restore this snapshot?', ()=>{
    learned = new Set(s.learned); fam = new Set(s.fam);
    saveProgress(); location.reload();
  });
}
function deleteSnap(i){
  snaps.splice(i,1);
  localStorage.setItem(K.snaps, JSON.stringify(snaps));
  renderSnaps();
}

// ── CSV Export ────────────────────────────────────────────────────────────────
function exportCSV(){
  const rows = allRows.filter(r=>r.tr.style.display!=='none');
  let csv = '\uFEFF#,Arabic,Root,Plural,EN,RU,Example AR,Example RU,Tier\n';
  rows.forEach((r,i)=>{
    const w = r.w;
    const root = normalizeRoot(w.r, w.w);
    csv += [i+1, w.w, root||'', w.pl||'', w.en||'', w.ru||'', (w.xa||'').replace(/"/g,'""'), (w.xr||'').replace(/"/g,'""'), w.tier].map(c=>'\"'+c+'\"').join(',') + '\n';
  });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  a.download = `Arabic_${allRows.length}.csv`; a.click();
}

// ── Reset ─────────────────────────────────────────────────────────────────────
function resetAll(){
  confirm2('Reset all progress? This cannot be undone.', ()=>{
    [K.learned, K.fam, K.snaps].forEach(k=>localStorage.removeItem(k));
    location.reload();
  });
}

// ── Confirm Dialog ────────────────────────────────────────────────────────────
function confirm2(msg, cb){
  confirmCb = cb;
  $('cdx-msg').textContent = msg;
  $('cdx-confirm').classList.add('open');
}

// ── Drag Reorder ──────────────────────────────────────────────────────────────
function initDrag(){
  const tbl = $('vocab-table');
  tbl.addEventListener('dragstart', e=>{
    const tr = e.target.closest('tr.word-row'); if(!tr) return;
    dragSrc = tr; tr.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });
  tbl.addEventListener('dragover', e=>{
    e.preventDefault();
    const tr = e.target.closest('tr.word-row');
    if(!tr || tr===dragSrc) return;
    $$('tr.drag-over').forEach(r=>r.classList.remove('drag-over'));
    tr.classList.add('drag-over');
  });
  tbl.addEventListener('drop', e=>{
    e.preventDefault();
    const tr = e.target.closest('tr.word-row'); if(!tr||tr===dragSrc) return;
    tr.parentNode.insertBefore(dragSrc, tr);
    $$('tr.drag-over').forEach(r=>r.classList.remove('drag-over'));
    renum();
  });
  tbl.addEventListener('dragend', ()=>{
    if(dragSrc) dragSrc.classList.remove('dragging'); dragSrc = null;
    $$('tr.drag-over').forEach(r=>r.classList.remove('drag-over'));
  });
}

// ── Study Mode ────────────────────────────────────────────────────────────────
function startStudy(){
  const pool = allRows.filter(r=>r.tr.style.display!=='none' && !learned.has(r.key));
  if(!pool.length){ alert('No words to study!'); return; }
  studyDeck = [...pool].sort(()=>Math.random()-.5);
  studyIdx = 0; studyKnown = 0;
  $('study-overlay').classList.add('open');
  showStudyCard();
  logEvent('study_start', { pool: studyDeck.length });
}

function showStudyCard(){
  const front = $('study-word');
  const back  = $('study-back');
  const fill  = $('s-prog-fill');
  const prog  = $('s-prog-txt');

  if(studyIdx >= studyDeck.length){
    front.innerHTML = `<div style="text-align:center">
      <div style="font-size:36px;color:var(--pal-accent)">${studyKnown}</div>
      <div style="font-size:14px">known of ${studyDeck.length}</div></div>`;
    back.style.display = 'none';
    $('s-skip').style.display = 'none';
    $('s-hard').style.display = 'none';
    $('s-know').innerHTML     = 'Done &#x2713;';
    if(fill) fill.style.width = '100%';
    if(prog) prog.textContent = `${studyKnown}/${studyDeck.length} known`;
    return;
  }

  const entry = studyDeck[studyIdx];
  const w     = entry.w;
  front.textContent  = displayAr(w.w);
  front.dataset.ar   = w.w;
  back.style.display = 'none';
  back.innerHTML = `
    <div class="sb-trans">${esc(currentLang==='ru' ? (w.ru||w.en||'') : (w.en||w.ru||''))}</div>
    <span class="sb-ar">${esc(displayAr(w.xa||''))}</span>
    <span class="sb-tr">${esc(currentLang==='ru' ? (w.xr||w.xe||'') : (w.xe||w.xr||''))}</span>`;
  $('s-skip').style.display = '';
  $('s-hard').style.display = '';
  $('s-know').innerHTML     = '<span class="ui-ru">&#x2713; Знаю</span><span class="ui-en">&#x2713; Know</span>';

  const pct = Math.round(studyIdx/studyDeck.length*100);
  if(fill) fill.style.width = pct+'%';
  if(prog) prog.textContent = `${studyIdx+1}/${studyDeck.length}`;
}

// ── Quiz ──────────────────────────────────────────────────────────────────────
function startQuiz(){
  const sz   = Math.max(5, Math.min(200, parseInt(($('quiz-size')||{}).value)||20));
  const pool = allRows.filter(r=>r.tr.style.display!=='none');
  if(pool.length < 4){ alert('Need at least 4 visible words.'); return; }
  quizWords   = [...pool].sort(()=>Math.random()-.5).slice(0, sz);
  quizIdx     = 0; quizCorrect = 0; quizPending = null;
  $('quiz-overlay').classList.add('open');
  $('quiz-summary').style.display = 'none';
  showQuizCard();
  logEvent('quiz_start', { size: sz });
}

function showQuizCard(){
  const qWord  = $('quiz-word');
  const qGrid  = $('quiz-grid');
  const qMeta  = $('quiz-meta');
  const qSumm  = $('quiz-summary');

  if(quizIdx >= quizWords.length){
    if(qWord) qWord.style.display = 'none';
    if(qGrid) qGrid.style.display = 'none';
    if(qMeta) qMeta.style.display = 'none';
    if(qSumm){
      qSumm.style.display = 'block';
      qSumm.innerHTML = `<div class="qs-score">${quizCorrect}/${quizWords.length}</div>
        <p class="qs-sub">${Math.round(quizCorrect/quizWords.length*100)}% correct</p>
        <button class="tb-btn" onclick="document.getElementById('quiz-overlay').classList.remove('open')" style="margin-top:12px">Close</button>`;
    }
    return;
  }

  if(qWord) qWord.style.display = '';
  if(qGrid) qGrid.style.display = 'grid';
  if(qMeta) qMeta.style.display = 'flex';

  const entry   = quizWords[quizIdx];
  const w       = entry.w;
  const correct = currentLang==='ru' ? (w.ru||w.en||'') : (w.en||w.ru||'');
  if(qWord) qWord.textContent = displayAr(w.w);
  if($('q-prog'))  $('q-prog').textContent = `${quizIdx+1}/${quizWords.length}`;
  if($('q-score')) $('q-score').innerHTML  = `&#x2713; ${quizCorrect}`;

  const pool = AR_WORDS.filter(x=>x.w!==w.w);
  const choices = [correct];
  while(choices.length < 4 && pool.length){
    const rand = pool.splice(Math.floor(Math.random()*pool.length),1)[0];
    const t = currentLang==='ru' ? (rand.ru||rand.en||'') : (rand.en||rand.ru||'');
    if(!choices.includes(t)) choices.push(t);
  }
  choices.sort(()=>Math.random()-.5);

  if(qGrid){
    qGrid.innerHTML = '';
    choices.forEach(c=>{
      const btn = document.createElement('button');
      btn.className = 'quiz-choice';
      btn.textContent = c;
      btn.onclick = ()=>{
        if(quizPending) return;
        quizPending = setTimeout(()=>{ quizPending=null; quizIdx++; showQuizCard(); }, 850);
        $$('.quiz-choice').forEach(b=>b.style.pointerEvents='none');
        if(c === correct){ btn.classList.add('correct'); quizCorrect++; }
        else {
          btn.classList.add('wrong');
          $$('.quiz-choice').forEach(b=>{ if(b.textContent===correct) b.classList.add('correct'); });
        }
      };
      qGrid.appendChild(btn);
    });
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function(){
  loadState();
  buildTable();
  updateStats();
  updateSecHdrs();
  initDrag();
  applyFontPrefs();
  renderSnaps();

  if(window.speechSynthesis){
    initVoices();
    window.speechSynthesis.onvoiceschanged = initVoices;
  }

  // Active state restore
  const mode = localStorage.getItem(K.mode)||'light';
  $$('[data-mode]').forEach(b=>b.classList.toggle('active', b.dataset.mode===mode));
  $$('[data-lang]').forEach(b=>b.classList.toggle('active', b.dataset.lang===currentLang));
  const pal = localStorage.getItem(K.pal)||'rose';
  $$('.pal-btn').forEach(b=>b.classList.toggle('active', b.dataset.pal===pal));
  // Initial active state for tier + POS filters
  $$('.tier-filter-btn').forEach(b=>{
    const t = parseInt(b.dataset.tier)||0;
    b.classList.toggle('active', t===0 && filterTiers.size===0);
  });
  $$('.pos-filter-btn').forEach(b=>{
    const p = (b.dataset.pos||'').trim();
    b.classList.toggle('active', p === filterPos);
  });
  $$('.subpos-filter-btn').forEach(b=>{
    const sp = (b.dataset.subpos||'').trim();
    b.classList.toggle('active', !filterSubpos && sp === '');
  });
  $$('.letter-filter-btn').forEach(b=>{
    const l = b.dataset.letter || '';
    b.classList.toggle('active', !filterLetter && l === '');
  });
  $$('[data-col-toggle]').forEach(b=>{
    const col = b.dataset.colToggle;
    b.classList.toggle('active', body.classList.contains('hide-'+col));
  });
  const tb = $('tashkeel-btn'); if(tb) tb.classList.toggle('active', tashkeelOn);

  // Mode buttons
  $$('[data-mode]').forEach(b=>b.addEventListener('click',()=>setMode(b.dataset.mode)));
  // Lang buttons
  $$('[data-lang]').forEach(b=>b.addEventListener('click',()=>setLang(b.dataset.lang)));
  // Palette
  $$('.pal-btn').forEach(b=>b.addEventListener('click',()=>setPalette(b.dataset.pal)));

  // Tashkeel toggle
  const tshBtn = $('tashkeel-btn');
  if(tshBtn) tshBtn.addEventListener('click',()=>setTashkeel(!tashkeelOn));

  // TTS rate
  const rateEl = $('tts-rate');
  if(rateEl) rateEl.addEventListener('change',()=>{ ttsRate = parseFloat(rateEl.value); });

  // TTS button clicks
  document.addEventListener('click', e=>{
    const btn = e.target.closest('.tts-btn');
    if(btn) speak(btn.dataset.ar||'', btn);
  });
  // Root family expander toggle
  document.addEventListener('click', e=>{
    const rb = e.target.closest('.root-btn');
    if(!rb) return;
    const tr = rb.closest('tr.word-row'); if(!tr) return;
    toggleRootFamily(tr);
  });
  // Root family item click (jump to word)
  document.addEventListener('click', e=>{
    const rf = e.target.closest('.rf-item');
    if(!rf) return;
    const word = rf.dataset.word || '';
    const row = wMap.get(word);
    if(row){
      row.scrollIntoView({behavior:'smooth', block:'center'});
      row.classList.add('flash');
      setTimeout(()=>row.classList.remove('flash'), 800);
    }
  });
  // Click on Arabic word to speak
  document.addEventListener('click', e=>{
    const ar = e.target.closest('.ar');
    if(!ar) return;
    const tr = ar.closest('tr.word-row'); if(!tr) return;
    speak(tr.dataset.key);
  });

  // Checkboxes
  document.addEventListener('change', e=>{
    const tr = e.target.closest('tr.word-row'); if(!tr) return;
    if(e.target.classList.contains('learn-cb')) onLearned(tr, e.target);
    if(e.target.classList.contains('fam-cb'))   onFam(tr, e.target);
  });

  // Group collapse
  document.addEventListener('click', e=>{
    const gh = e.target.closest('.grp-hdr');
    if(gh) togglePosGroup(gh);
  });

  // Search
  const srch = $('search');
  const clrBtn = $('search-clear');
  if(srch){
    srch.addEventListener('input',()=>{
      clearTimeout(searchTimer);
      clrBtn.style.display = srch.value ? 'block' : 'none';
      searchTimer = setTimeout(()=>doSearch(srch.value), 130);
    });
  }
  if(clrBtn) clrBtn.addEventListener('click',()=>{
    srch.value = ''; clrBtn.style.display = 'none'; doSearch(''); srch.focus();
  });

  // Tier filter buttons
  $$('.tier-filter-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const t = parseInt(btn.dataset.tier)||0;
      if(t === 0){
        filterTiers.clear();
      } else {
        if(filterTiers.has(t)) filterTiers.delete(t);
        else filterTiers.add(t);
      }
      $$('.tier-filter-btn').forEach(b=>{
        const bt = parseInt(b.dataset.tier)||0;
        b.classList.toggle('active', bt===0 ? filterTiers.size===0 : filterTiers.has(bt));
      });
      applyFilters();
      logEvent('filter_tier', { tiers: Array.from(filterTiers.values()) });
    });
  });

  // Letter filter buttons (Arabic alphabet)
  $$('.letter-filter-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const l = btn.dataset.letter || '';
      if(!l){
        filterLetter = '';
      } else {
        filterLetter = (filterLetter === l) ? '' : l;
      }
      $$('.letter-filter-btn').forEach(b=>{
        const bl = b.dataset.letter || '';
        b.classList.toggle('active', (!filterLetter && !bl) || (bl && bl === filterLetter));
      });
      applyFilters();
      logEvent('filter_letter', { letter: filterLetter || 'all' });
    });
  });

  // POS filter buttons (ism / fi'l / harf)
  $$('.pos-filter-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const p = (btn.dataset.pos||'').trim();
      filterPos = (filterPos === p) ? '' : p;
      $$('.pos-filter-btn').forEach(b=>{
        b.classList.toggle('active', (b.dataset.pos||'').trim() === filterPos);
      });
      if(!filterPos){
        // make sure "all" is active
        $$('.pos-filter-btn').forEach(b=>{
          if(!(b.dataset.pos||'').trim()) b.classList.add('active');
        });
      }
      applyFilters();
      logEvent('filter_pos', { pos: filterPos || 'all' });
    });
  });

  // Sub-division filters (question words, pronouns, etc.)
  $$('.subpos-filter-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const sp = (btn.dataset.subpos||'').trim();
      filterSubpos = (filterSubpos === sp) ? '' : sp;
      $$('.subpos-filter-btn').forEach(b=>{
        b.classList.toggle('active', (b.dataset.subpos||'').trim() === filterSubpos);
      });
      if(!filterSubpos){
        $$('.subpos-filter-btn').forEach(b=>{
          if(!(b.dataset.subpos||'').trim()) b.classList.add('active');
        });
      }
      applyFilters();
      logEvent('filter_subpos', { subpos: filterSubpos || 'all' });
    });
  });

  // Column toggles
  ['num','word','root','trans','ex'].forEach(c=>{
    const b = $('col-btn-'+c);
    if(b){
      b.classList.toggle('active', body.classList.contains('hide-'+c));
      b.addEventListener('click',()=>toggleCol(c));
    }
  });

  // Font panel
  const fpBtn = $('font-panel-btn');
  const fp    = $('font-panel');
  const spBtn = $('snap-btn');
  const sp    = $('snap-panel');
  if(fpBtn&&fp) fpBtn.addEventListener('click',e=>{ e.stopPropagation(); fp.classList.toggle('open'); sp&&sp.classList.remove('open'); });
  if(spBtn&&sp) spBtn.addEventListener('click',e=>{ e.stopPropagation(); sp.classList.toggle('open'); fp&&fp.classList.remove('open'); });

  ['fp-font-ar','fp-font-ru'].forEach(id=>{ const el=$(id); if(el) el.addEventListener('change',saveFontPrefs); });
  ['fp-sz-ar','fp-sz-ru'].forEach(id=>{
    const el=$(id), vEl=$(id+'-val');
    if(el) el.addEventListener('input',()=>{ if(vEl) vEl.textContent=el.value; saveFontPrefs(); });
  });

  // Snap actions
  const capBtn = $('snap-capture');
  if(capBtn) capBtn.addEventListener('click', captureSnap);
  const sl = $('snap-list');
  if(sl) sl.addEventListener('click', e=>{
    const btn = e.target.closest('[data-restore],[data-del]'); if(!btn) return;
    if(btn.dataset.restore !== undefined) restoreSnap(parseInt(btn.dataset.restore));
    else if(btn.dataset.del !== undefined) deleteSnap(parseInt(btn.dataset.del));
  });

  // Confirm dialog
  const cdxOk  = $('cdx-ok');
  const cdxCnl = $('cdx-cancel');
  if(cdxOk)  cdxOk.addEventListener('click', ()=>{ $('cdx-confirm').classList.remove('open'); if(confirmCb) confirmCb(); confirmCb=null; });
  if(cdxCnl) cdxCnl.addEventListener('click',()=>{ $('cdx-confirm').classList.remove('open'); confirmCb=null; });

  // Reset & Export
  const resetBtn = $('reset-btn'); if(resetBtn) resetBtn.addEventListener('click', resetAll);
  const expBtn   = $('export-btn'); if(expBtn)  expBtn.addEventListener('click',  exportCSV);

  // Study
  const studyBtn = $('study-btn');  if(studyBtn)  studyBtn.addEventListener('click',  startStudy);
  const studyX   = $('study-close'); if(studyX)    studyX.addEventListener('click',   ()=>$('study-overlay').classList.remove('open'));
  const sw = $('study-word');
  if(sw) sw.addEventListener('click',()=>{ speak(sw.dataset.ar); $('study-back').style.display='block'; });
  const sSkip = $('s-skip'); if(sSkip) sSkip.addEventListener('click',()=>{ studyIdx++; showStudyCard(); });
  const sHard = $('s-hard'); if(sHard) sHard.addEventListener('click',()=>{ studyDeck.push(studyDeck[studyIdx]); studyIdx++; showStudyCard(); });
  const sKnow = $('s-know'); if(sKnow) sKnow.addEventListener('click',()=>{ studyKnown++; studyIdx++; showStudyCard(); });

  // Quiz
  const quizBtn = $('quiz-btn');  if(quizBtn)  quizBtn.addEventListener('click',  startQuiz);
  const quizX   = $('quiz-close'); if(quizX)   quizX.addEventListener('click',    ()=>$('quiz-overlay').classList.remove('open'));

  // News overlay
  const newsBtn = $('news-btn');
  const newsOv  = $('news-overlay');
  const newsX   = $('news-close');
  const newsSel = $('news-select');
  const newsSelInline = $('news-select-inline');
  const newsVid = $('news-player');
  function setNewsChannel(url){
    if(!newsVid) return;
    try{ newsVid.pause(); }catch(e){}
    newsVid.src = url || '';
    newsVid.load();
    if(url){
      const p = newsVid.play();
      if(p && p.catch) p.catch(()=>{});
    }
  }
  function newsLabel(ch){
    if(ch.label && ch.label !== 'auto') return ch.name + ' (' + ch.label + ')';
    return ch.name;
  }
  if(NEWS_CHANNELS.length){
    if(newsSel) newsSel.innerHTML = NEWS_CHANNELS.map(ch=>`<option value="${ch.url}">${newsLabel(ch)}</option>`).join('');
    if(newsSelInline) newsSelInline.innerHTML = NEWS_CHANNELS.map(ch=>`<option value="${ch.url}">${newsLabel(ch)}</option>`).join('');
    const defaultUrl = NEWS_CHANNELS[0].url;
    if(newsSel) newsSel.value = defaultUrl;
    if(newsSelInline) newsSelInline.value = defaultUrl;
    setNewsChannel(defaultUrl);
    if(newsSel) newsSel.addEventListener('change', ()=>{
      if(newsSelInline) newsSelInline.value = newsSel.value;
      setNewsChannel(newsSel.value);
    });
    if(newsSelInline) newsSelInline.addEventListener('change', ()=>{
      if(newsSel) newsSel.value = newsSelInline.value;
      setNewsChannel(newsSelInline.value);
    });
  }
  if(newsBtn && newsOv){
    newsBtn.addEventListener('click', ()=>{
      newsOv.classList.add('open');
      var url = (newsSelInline && newsSelInline.value) ? newsSelInline.value : (newsSel ? newsSel.value : '');
      if(url) setNewsChannel(url);
    });
  }
  function closeNews(){
    if(newsOv) newsOv.classList.remove('open');
    if(newsVid) try{ newsVid.pause(); }catch(e){}
  }
  if(newsX) newsX.addEventListener('click', closeNews);
  if(newsOv) newsOv.addEventListener('click', e=>{ if(e.target === newsOv) closeNews(); });

  // Collapse/Expand all
  const colAll = $('collapse-all');
  const expAll = $('expand-all');
  if(colAll) colAll.addEventListener('click',()=>{ $$('.grp-hdr').forEach(g=>{ if(!g.classList.contains('collapsed')) togglePosGroup(g); }); });
  if(expAll) expAll.addEventListener('click',()=>{ $$('.grp-hdr').forEach(g=>{ if(g.classList.contains('collapsed'))  togglePosGroup(g); }); });

  // Keyboard shortcuts
  const se = $('search');
  document.addEventListener('keydown', e=>{
    if((e.key==='/' || (e.key==='f'&&e.ctrlKey)) && document.activeElement!==se){
      e.preventDefault(); if(se) se.focus();
    }
    if(e.key==='Escape'){
      if(se && se.value){ se.value=''; doSearch(''); $('search-clear').style.display='none'; return; }
      if(fp) fp.classList.remove('open');
      if(sp) sp.classList.remove('open');
    }
  });

  // Close panels on outside click
  document.addEventListener('click', e=>{
    if(fp && !e.target.closest('#font-panel') && !e.target.closest('#font-panel-btn')) fp.classList.remove('open');
    if(sp && !e.target.closest('#snap-panel') && !e.target.closest('#snap-btn'))       sp.classList.remove('open');
  });

  renum();
});

})();
