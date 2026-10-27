const DEFAULTS = { baseUrl: 'http://127.0.0.1:8765', token: '', maxChars: 8000 };

function $(id) { return document.getElementById(id); }

function setStatus(text, cls) {
  const el = $('status');
  el.textContent = text;
  el.className = cls || '';
}

async function load() {
  const cfg = Object.assign({}, DEFAULTS, await chrome.storage.sync.get(DEFAULTS));
  $('baseUrl').value = cfg.baseUrl;
  $('token').value = cfg.token;
  $('maxChars').value = cfg.maxChars;
}

async function save() {
  const cfg = {
    baseUrl: $('baseUrl').value.trim() || DEFAULTS.baseUrl,
    token: $('token').value.trim(),
    maxChars: Math.max(500, Math.min(30000, Number($('maxChars').value) || DEFAULTS.maxChars)),
  };
  await chrome.storage.sync.set(cfg);
  setStatus('已保存', 'ok');
  setTimeout(() => setStatus(''), 2000);
}

async function test() {
  setStatus('测试中…');
  try {
    const base = ($('baseUrl').value.trim() || DEFAULTS.baseUrl).replace(/\/+$/, '');
    const headers = {};
    const token = $('token').value.trim();
    if (token) headers['X-Token'] = token;
    const res = await fetch(base + '/api/health', { headers });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    setStatus(data.llm_enabled ? `连接成功 · AI ${data.model}` : '连接成功 · 未配置 AI', 'ok');
  } catch (err) {
    setStatus('连接失败：' + err.message, 'err');
  }
}

$('save').addEventListener('click', save);
$('test').addEventListener('click', test);
load();
