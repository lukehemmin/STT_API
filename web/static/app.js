'use strict';
/* STT 웹 테스트 - API와 별개 주소에서 동작하는 정적 클라이언트.
 * API 주소 우선순위: ?api= 쿼리 > /config.js(서버가 주입) > 저장된 값 > 같은 호스트의 :8000
 * 같은 파일을 로컬(8000/8080)과 Colab(터널 두 개) 양쪽에서 그대로 쓴다. */

const $ = (id) => document.getElementById(id);
const S = { file: null, jobId: null, running: false, cancel: false, result: {}, fmt: 'txt' };

function defaultApiBase() {
  const q = new URLSearchParams(location.search).get('api');
  if (q) return q;
  const injected = (window.STT_CONFIG && window.STT_CONFIG.apiBase) || '';
  if (injected) return injected;
  try {
    const saved = localStorage.getItem('stt.apiBase');
    if (saved) return saved;
  } catch (_) { /* 저장소 사용 불가 */ }
  return location.protocol + '//' + location.hostname + ':8000';
}

function apiBase() {
  let base = $('apiUrl').value.trim();
  while (base.endsWith('/')) base = base.slice(0, -1);
  return base;
}

function authHeaders() {
  const key = $('apiKey').value.trim();
  return key ? { Authorization: 'Bearer ' + key } : {};
}

function api(path, options) {
  const opts = options || {};
  return fetch(apiBase() + path, { ...opts, headers: { ...authHeaders(), ...(opts.headers || {}) } });
}

async function errorMessage(res) {
  try {
    const e = (await res.json()).error || {};
    return (res.status + ' ' + (e.code || '') + ' ' + (e.message || '')).trim();
  } catch (_) {
    return res.status + ' ' + res.statusText;
  }
}

async function checkApi() {
  const badge = $('health');
  badge.className = 'badge';
  badge.textContent = '확인 중...';
  $('engineInfo').textContent = '';
  try {
    const res = await api('/v1/info');
    if (!res.ok) throw new Error(await errorMessage(res));
    const info = await res.json();
    const eng = info.engine || {};
    const limits = info.limits || {};
    badge.className = 'badge ok';
    badge.textContent = '연결됨';
    $('engineInfo').textContent = '모델 ' + (eng.model || eng.model_name || '?') + ' / ' + (eng.device || '?')
      + ' / 최대 업로드 ' + (limits.max_upload_mb || '?') + ' MB';
    try { localStorage.setItem('stt.apiBase', apiBase()); } catch (_) { /* 무시 */ }
  } catch (err) {
    badge.className = 'badge bad';
    badge.textContent = '연결 실패';
    $('engineInfo').textContent = err.message + ' - 주소, API 키, CORS(STT_CORS_ORIGINS)를 확인하세요.';
  }
}

function fmtSize(bytes) {
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
  return Math.max(1, Math.round(bytes / 1024)) + ' KB';
}

function setFile(file) {
  S.file = file || null;
  $('fileInfo').textContent = file ? file.name + ' (' + fmtSize(file.size) + ')' : '선택된 파일 없음';
  $('btnStart').disabled = !file || S.running;
}

function wireDropzone() {
  const zone = $('dropzone');
  const input = $('fileInput');
  zone.addEventListener('click', () => input.click());
  input.addEventListener('click', (e) => e.stopPropagation());
  zone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  input.addEventListener('change', () => setFile(input.files[0]));
  ['dragenter', 'dragover'].forEach((t) => zone.addEventListener(t, (e) => {
    e.preventDefault();
    zone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((t) => zone.addEventListener(t, (e) => {
    e.preventDefault();
    zone.classList.remove('over');
  }));
  zone.addEventListener('drop', (e) => {
    if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  });
}

function init() {
  $('apiUrl').value = defaultApiBase();
  wireDropzone();
  $('btnCheck').addEventListener('click', checkApi);
  checkApi();
}

const STATUS_LABEL = {
  queued: '대기 중',
  preprocessing: '오디오 변환 중',
  transcribing: '전사 중',
  postprocessing: '마무리 중',
  completed: '완료',
  failed: '실패',
  canceled: '취소됨',
};
const TERMINAL = ['completed', 'failed', 'canceled'];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function showError(message) {
  const box = $('errorBox');
  box.textContent = message || '';
  box.hidden = !message;
}

function setRunning(running) {
  S.running = running;
  $('btnStart').disabled = running || !S.file;
  $('btnCancel').hidden = !running;
}

function setProgress(value, text) {
  $('progressWrap').hidden = false;
  $('bar').style.width = Math.round(Math.max(0, Math.min(1, value)) * 100) + '%';
  $('statusText').textContent = text;
}

async function pollJob(id) {
  let failures = 0;
  while (true) {
    let job;
    try {
      const res = await api('/v1/transcriptions/' + id);
      if (!res.ok) throw new Error(await errorMessage(res));
      job = await res.json();
      failures = 0;
    } catch (err) {
      failures += 1;
      if (failures >= 5) throw err;
      await sleep(1500);
      continue;
    }
    const pct = Math.round((job.progress || 0) * 100);
    setProgress(job.progress || 0, (STATUS_LABEL[job.status] || job.status) + ' · ' + pct + '%');
    if (TERMINAL.includes(job.status)) return job;
    await sleep(1000);
  }
}

async function startJob() {
  if (!S.file || S.running) return;
  showError('');
  $('resultSection').hidden = true;
  S.cancel = false;
  S.jobId = null;
  setRunning(true);
  setProgress(0, '업로드 중... (' + fmtSize(S.file.size) + ')');
  try {
    const form = new FormData();
    form.append('file', S.file, S.file.name);
    const lang = $('language').value;
    if (lang) form.append('language', lang); // 비우면 서버가 자동 감지
    form.append('timestamps', $('timestamps').value);
    const res = await api('/v1/transcriptions', { method: 'POST', body: form });
    if (res.status !== 202) throw new Error(await errorMessage(res));
    S.jobId = (await res.json()).id;
    const job = await pollJob(S.jobId);
    if (job.status === 'completed') {
      await loadResults(job);
    } else if (job.status === 'failed') {
      const e = job.error || {};
      throw new Error('전사 실패: ' + (e.code || '') + ' ' + (e.message || ''));
    } else {
      setProgress(job.progress || 0, STATUS_LABEL[job.status] || job.status);
    }
  } catch (err) {
    showError(err.message);
    $('progressWrap').hidden = true;
  } finally {
    setRunning(false);
  }
}

async function cancelJob() {
  if (!S.jobId) return;
  S.cancel = true;
  try {
    const res = await api('/v1/transcriptions/' + S.jobId + '/cancel', { method: 'POST' });
    if (!res.ok && res.status !== 409) throw new Error(await errorMessage(res));
  } catch (err) {
    showError(err.message);
  }
}

$('btnStart').addEventListener('click', startJob);
$('btnCancel').addEventListener('click', cancelJob);
const MIME = { txt: 'text/plain', srt: 'text/plain', vtt: 'text/vtt', json: 'application/json' };

function showTab(fmt) {
  S.fmt = fmt;
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.fmt === fmt));
  $('output').textContent = S.result[fmt] || '(내용 없음)';
}

async function loadResults(job) {
  setProgress(1, '결과 가져오는 중...');
  const withWords = $('timestamps').value === 'word' ? '&include_words=true' : '';
  const base = '/v1/transcriptions/' + job.id + '/result?format=';
  const responses = await Promise.all([
    api(base + 'json' + withWords),
    api(base + 'txt'),
    api(base + 'srt'),
    api(base + 'vtt'),
  ]);
  for (const r of responses) {
    if (!r.ok) throw new Error(await errorMessage(r));
  }
  const body = await responses[0].json();
  S.result = {
    json: JSON.stringify(body, null, 2),
    txt: await responses[1].text(),
    srt: await responses[2].text(),
    vtt: await responses[3].text(),
  };
  const prob = body.language_probability;
  const lang = body.language
    ? body.language + (prob != null ? ' (' + Math.round(prob * 100) + '%)' : '')
    : '알 수 없음';
  $('meta').textContent = '언어 ' + lang
    + ' · 길이 ' + ((body.duration_ms || 0) / 1000).toFixed(1) + '초'
    + ' · 세그먼트 ' + (body.segments || []).length + '개'
    + ' · 모델 ' + (body.model || '?');
  setProgress(1, '완료');
  $('resultSection').hidden = false;
  showTab('txt');
}

async function copyOutput() {
  const text = S.result[S.fmt] || '';
  const btn = $('btnCopy');
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = '복사됨';
  } catch (_) {
    // 클립보드 권한이 없으면 내용을 선택만 해 둔다.
    const range = document.createRange();
    range.selectNodeContents($('output'));
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    btn.textContent = '선택됨 (Ctrl+C)';
  }
  setTimeout(() => { btn.textContent = '복사'; }, 1500);
}

function downloadOutput() {
  const text = S.result[S.fmt];
  if (!text) return;
  const name = S.file ? S.file.name : 'transcript';
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const url = URL.createObjectURL(new Blob([text], { type: MIME[S.fmt] + ';charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = stem + '.' + S.fmt;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$('tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-fmt]');
  if (btn) showTab(btn.dataset.fmt);
});
$('btnCopy').addEventListener('click', copyOutput);
$('btnDownload').addEventListener('click', downloadOutput);

init();
