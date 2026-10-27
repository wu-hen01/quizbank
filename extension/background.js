const DEFAULTS = {
  baseUrl: 'http://127.0.0.1:8765',
  token: '',
  maxChars: 8000,
};

async function getConfig() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return Object.assign({}, DEFAULTS, stored);
}

function notify(title, message) {
  chrome.notifications.create({
    type: 'basic',
    iconUrl: 'icon128.png',
    title,
    message: String(message || '').slice(0, 180),
  });
}

function flashBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color: color || '#16a34a' });
  setTimeout(() => chrome.action.setBadgeText({ text: '' }), 2500);
}

async function postCapture(config, path, payload) {
  const headers = { 'Content-Type': 'application/json' };
  if (config.token) headers['X-Token'] = config.token;

  const url = config.baseUrl.replace(/\/+$/, '') + path;
  let response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });
  } catch (err) {
    // fetch 抛错基本就是服务没起来，给个能照着做的提示
    throw new Error(`连不上本地服务 ${url}：先双击 start.bat 启动 QuizBank`);
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  return data;
}

function reportSuccess(data, extra) {
  const count = Number(data && data.count) || 1;
  const stem = (data && data.question && data.question.stem) || '题目';
  const prefix = count > 1 ? `已收录 ${count} 道题` : (data.created ? '已收录' : '已更新（重复题）');
  const parts = [];
  if (extra) parts.push(extra);
  parts.push(stem.slice(0, 70));
  notify(prefix, parts.join(' · '));
  flashBadge(count > 1 ? String(count) : (data.created ? '✓' : '↻'));
}

function reportError(err) {
  const message = String((err && err.message) || err);
  notify('收录失败', message);
  flashBadge('!', '#dc2626');
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: 'quizbank-save',
      title: '收录到 QuizBank',
      contexts: ['selection', 'page'],
    });
    chrome.contextMenus.create({
      id: 'quizbank-image',
      title: '收录这张图片到 QuizBank（OCR）',
      contexts: ['image'],
    });
  });
});

chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});

/* 取选区：所有 frame 的 getSelection，取最长的一段 */
async function readSelection(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => (window.getSelection ? String(window.getSelection()) : ''),
    });
    const parts = (results || [])
      .map((r) => (r && typeof r.result === 'string' ? r.result : '').trim())
      .filter(Boolean)
      .sort((a, b) => b.length - a.length);
    return parts[0] || '';
  } catch (err) {
    return '';
  }
}

/* 兜底：整页文字，优先主内容区，尽量避开左侧导航 */
async function readPage(tabId, maxChars) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      args: [maxChars],
      func: (limit) => {
        const pick = (selector) => {
          const el = document.querySelector(selector);
          return el && el.innerText ? el.innerText : '';
        };
        const main =
          pick('main') ||
          pick('[role="main"]') ||
          pick('article') ||
          pick('#content') ||
          pick('.content') ||
          pick('#main') ||
          '';
        const body = document.body ? document.body.innerText || '' : '';
        return (main.length > 200 ? main : body).slice(0, limit);
      },
    });
    const parts = (results || [])
      .map((r) => (r && typeof r.result === 'string' ? r.result : '').trim())
      .filter(Boolean)
      .sort((a, b) => b.length - a.length);
    return parts[0] || '';
  } catch (err) {
    return '';
  }
}

/* 右键选中/整页文字 → 收录 */
async function handleText(info, tab, config) {
  const max = Number(config.maxChars) || 8000;

  // Chrome 在菜单点击事件里直接给出选中的文字，跨 iframe、
  // 富文本编辑器都可靠，优先用它。
  let text = String(info.selectionText || '').trim();
  let source = text ? 'selection' : '';

  if (!text) {
    text = (await readSelection(tab.id)).trim();
    if (text) source = 'selection';
  }
  if (!text) {
    text = (await readPage(tab.id, max)).trim();
    source = 'page';
  }

  text = text.replace(/\n{3,}/g, '\n\n').trim().slice(0, max);
  if (!text) {
    throw new Error('没有取到文字：先用鼠标选中题目，再右键');
  }

  const payload = {
    text,
    url: (tab && tab.url) || '',
    title: (tab && tab.title) || '',
    whole_page: source !== 'selection',
    selection_len: String(info.selectionText || '').length,
  };

  const data = await postCapture(config, '/api/capture', payload);
  reportSuccess(data, source === 'selection' ? `选区 ${text.length} 字` : `整页 ${text.length} 字`);
}

/* 右键图片 → OCR 收录 */
async function handleImage(info, tab, config) {
  const srcUrl = info.srcUrl || '';
  if (!srcUrl) throw new Error('这张图片没有可用的地址');

  let dataUrl = '';
  let pageError = '';
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      args: [srcUrl],
      func: async (url) => {
        async function viaFetch() {
          const resp = await fetch(url, { credentials: 'include' });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const blob = await resp.blob();
          if (!blob.type.startsWith('image/')) throw new Error('返回的不是图片');
          if (blob.size > 10 * 1024 * 1024) throw new Error('图片超过 10 MB');
          return await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error('读取图片失败'));
            reader.readAsDataURL(blob);
          });
        }

        async function viaCanvas() {
          const img = new Image();
          img.crossOrigin = 'anonymous';
          await new Promise((resolve, reject) => {
            img.onload = resolve;
            img.onerror = () => reject(new Error('图片无法加载'));
            img.src = url;
          });
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          canvas.getContext('2d').drawImage(img, 0, 0);
          return canvas.toDataURL('image/png');
        }

        try {
          return { dataUrl: await viaFetch() };
        } catch (first) {
          try {
            return { dataUrl: await viaCanvas() };
          } catch (second) {
            return { dataUrl: '', error: String((first && first.message) || first) };
          }
        }
      },
    });
    const out = results && results[0] ? results[0].result : null;
    dataUrl = (out && out.dataUrl) || '';
    pageError = (out && out.error) || '';
  } catch (err) {
    pageError = String((err && err.message) || err);
  }

  // dataURL 太大就交给服务端下载
  if (dataUrl && dataUrl.length > 9 * 1024 * 1024) dataUrl = '';

  const payload = {
    url: (tab && tab.url) || '',
    title: (tab && tab.title) || '',
  };
  if (dataUrl) payload.image = dataUrl;
  else payload.image_url = srcUrl;

  const data = await postCapture(config, '/api/capture/image', payload);
  if (data.ocr_text) reportSuccess(data, 'OCR');
  else reportSuccess(data, pageError ? '未识别 · ' + pageError : '');
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab || !tab.id) return;
  const config = await getConfig();

  try {
    if (info.menuItemId === 'quizbank-save') {
      await handleText(info, tab, config);
    } else if (info.menuItemId === 'quizbank-image') {
      await handleImage(info, tab, config);
    }
  } catch (err) {
    reportError(err);
  }
});
