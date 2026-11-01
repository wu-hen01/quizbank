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
  if (data && data.image_errors && data.image_errors.length) {
    parts.push('图片问题：' + data.image_errors[0]);
  }
  parts.push(stem.slice(0, 70));
  notify(prefix, parts.join(' · '));
  flashBadge(count > 1 ? String(count) : (data.created ? '✓' : '↻'));
}

function reportError(err) {
  const message = String((err && err.message) || err);
  notify('收录失败', message);
  flashBadge('!', '#dc2626');
}

function buildMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: 'quizbank-save',
      title: '收录到 QuizBank（文字 + 题干里的图）',
      contexts: ['selection', 'page'],
    });
    chrome.contextMenus.create({
      id: 'quizbank-image',
      title: '收录这张图片到 QuizBank（OCR）',
      contexts: ['image'],
    });
  });
}

chrome.runtime.onInstalled.addListener(buildMenus);
chrome.runtime.onStartup.addListener(buildMenus);

chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});

/* ==================================================================
   页面内执行：一次拿齐「选中的文字」+「题干里的图」
   - 选区里有 <img> 就都带上（多选题干配图、题干上方一张图都能覆盖）
   - 只选了文字时，往上找这道题的容器，把容器里的图也带上
   - 图优先转成 dataURL（页面内 fetch 带 cookie，同源基本都能成；
     不行再试 canvas；还不行就把绝对地址交给服务端下载）
   - onlySrc 有值时只转这一张（右键单张图片用）
   ================================================================== */
async function collectFromPage(limitChars, onlySrc) {
  const MAX_IMAGES = 6;
  const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
  const MIN_SIDE = 60;
  const MAX_BLOCK_TEXT = 2500;

  // 任何一个 frame 抛错都会让 executeScript 整体 reject，
  // 所以整段包起来，出错就返回空结果，让上层退回到"整页文字"。
  try {

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < MIN_SIDE || r.height < MIN_SIDE) return false;
    const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (st) {
      if (st.display === 'none' || st.visibility === 'hidden') return false;
      const op = parseFloat(st.opacity || '1');
      if (!isNaN(op) && op < 0.15) return false;
    }
    return true;
  }

  function junk(img) {
    const src = String(img.currentSrc || img.src || '');
    if (!src) return true;
    if (/^data:image\/svg/i.test(src)) return true;
    const hint = (
      src + ' ' + (img.className || '') + ' ' + (img.id || '') + ' ' + (img.alt || '')
    ).toLowerCase();
    return /(spacer|blank|pixel|1x1|placeholder|loading|avatar|logo|icon|emoji|qrcode|captcha)/.test(hint);
  }

  function usable(img) {
    if (!img) return false;
    if (!visible(img)) return false;
    if (junk(img)) return false;
    const nw = img.naturalWidth || 0;
    if (nw && nw < MIN_SIDE) return false;
    return true;
  }

  function dataUrlOf(url) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => {
        if (!done) {
          done = true;
          resolve(v);
        }
      };
      // 超时保护：别把右键菜单卡住
      setTimeout(() => finish(''), 8000);

      (async () => {
        try {
          const resp = await fetch(url, { credentials: 'include' });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          const blob = await resp.blob();
          if (!blob || blob.size > MAX_IMAGE_BYTES) throw new Error('too big');
          if (String(blob.type || '').indexOf('image/') !== 0) throw new Error('not image');
          const fr = new FileReader();
          fr.onload = () => finish(String(fr.result || ''));
          fr.onerror = () => finish('');
          fr.readAsDataURL(blob);
        } catch (e) {
          try {
            const im = new Image();
            im.crossOrigin = 'anonymous';
            im.onload = () => {
              try {
                const c = document.createElement('canvas');
                c.width = im.naturalWidth;
                c.height = im.naturalHeight;
                c.getContext('2d').drawImage(im, 0, 0);
                finish(c.toDataURL('image/png'));
              } catch (err) {
                finish('');
              }
            };
            im.onerror = () => finish('');
            im.src = url;
          } catch (err) {
            finish('');
          }
        }
      })();
    });
  }

  const sel = window.getSelection ? window.getSelection() : null;
  const text = sel ? String(sel) : '';

  if (onlySrc) {
    let one = '';
    try {
      one = await dataUrlOf(String(onlySrc));
    } catch (e) {
      one = '';
    }
    if (!one || one.length > MAX_IMAGE_BYTES * 1.4) one = String(onlySrc);
    return { text: text.slice(0, limitChars || 20000), images: [one], count: 1 };
  }

  const picked = [];
  const seenEl = new Set();
  const seenSrc = new Set();

  function add(img) {
    if (!img || seenEl.has(img)) return;
    if (!usable(img)) return;
    const src = String(img.currentSrc || img.src || '');
    if (!src || seenSrc.has(src)) return;
    seenEl.add(img);
    seenSrc.add(src);
    picked.push(img);
  }

  if (sel && sel.rangeCount > 0 && !sel.isCollapsed) {
    const range = sel.getRangeAt(0);
    const all = Array.prototype.slice.call(document.images || []);
    for (const img of all) {
      let hit = false;
      try {
        hit = range.intersectsNode(img);
      } catch (e) {
        hit = false;
      }
      if (hit) add(img);
    }

    // 只选了文字（题干里还夹着图）→ 往上找这道题的容器
    if (!picked.length) {
      let el = range.commonAncestorContainer;
      if (el && el.nodeType !== 1) el = el.parentElement;
      for (let i = 0; i < 8 && el && el !== document.body && el !== document.documentElement; i++) {
        const inside = Array.prototype.slice.call(el.querySelectorAll('img')).filter(usable);
        if (inside.length) {
          inside.slice(0, MAX_IMAGES).forEach(add);
          break;
        }
        if ((el.innerText || '').length > MAX_BLOCK_TEXT) break;
        el = el.parentElement;
      }
    }
  }

  const images = [];
  let total = 0;
  for (const img of picked.slice(0, MAX_IMAGES)) {
    const src = String(img.currentSrc || img.src || '');
    if (!src) continue;
    let out = '';
    try {
      out = await dataUrlOf(src);
    } catch (e) {
      out = '';
    }
    // 转不成 dataURL（跨域/防盗链）就把绝对地址交给服务端下载
    if (!out || out.length > MAX_IMAGE_BYTES * 1.4) out = src;
    // 一次带太多 base64 会把消息通道撑爆，超了就走服务端下载
    if (out.length > 1024 && total + out.length > 24 * 1024 * 1024) out = src;
    total += out.length;
    images.push(out);
  }

  return {
    text: text.slice(0, limitChars || 20000),
    images,
    count: images.length,
  };
  } catch (e) {
    return { text: '', images: [], count: 0 };
  }
}

/* 在所有 frame 里跑 collectFromPage，挑文字最多（没文字就图最多）的那个 */
async function collect(tabId, maxChars, onlySrc) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      args: [maxChars, onlySrc || ''],
      func: collectFromPage,
    });
    const items = (results || []).map((r) => (r && r.result) || null).filter(Boolean);
    if (!items.length) return null;
    items.sort((a, b) => {
      const ta = String(a.text || '').trim().length;
      const tb = String(b.text || '').trim().length;
      if (tb !== ta) return tb - ta;
      return (b.count || 0) - (a.count || 0);
    });
    return items[0];
  } catch (err) {
    return null;
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

/* 右键选中/整页文字 → 文字和图一起收录 */
async function handleText(info, tab, config) {
  const max = Number(config.maxChars) || 8000;

  // Chrome 在菜单点击事件里直接给出选中的文字，跨 iframe、
  // 富文本编辑器都可靠，优先用它。
  let text = String(info.selectionText || '').trim();
  let source = text ? 'selection' : '';
  let images = [];

  const page = await collect(tab.id, max);
  if (page) {
    if (!text) {
      text = String(page.text || '').trim();
      if (text) source = 'selection';
    }
    images = (page.images || []).filter(Boolean);
  }

  if (!text && !images.length) {
    text = (await readPage(tab.id, max)).trim();
    source = 'page';
  }

  text = text.replace(/\n{3,}/g, '\n\n').trim().slice(0, max);
  if (!text && !images.length) {
    throw new Error('没有取到文字或图片：先用鼠标选中题目，再右键');
  }

  const payload = {
    text,
    images,
    url: (tab && tab.url) || '',
    title: (tab && tab.title) || '',
    whole_page: source !== 'selection',
    selection_len: String(info.selectionText || '').length,
  };

  const data = await postCapture(config, '/api/capture', payload);
  const bits = [];
  if (text) bits.push((source === 'page' ? '整页 ' : '选区 ') + text.length + ' 字');
  if (images.length) bits.push('配图 ' + images.length + ' 张');
  reportSuccess(data, bits.join(' + '));
}

/* 右键单张图片 → 这张图 + 当前选区文字一起收录（OCR） */
async function handleImage(info, tab, config) {
  const srcUrl = info.srcUrl || '';
  if (!srcUrl) throw new Error('这张图片没有可用的地址');

  const page = await collect(tab.id, Number(config.maxChars) || 8000, srcUrl);
  const image = page && page.images && page.images[0] ? page.images[0] : srcUrl;
  const text = String(info.selectionText || (page && page.text) || '').trim();

  const payload = {
    text,
    images: [image],
    ocr: true,
    url: (tab && tab.url) || '',
    title: (tab && tab.title) || '',
  };

  const data = await postCapture(config, '/api/capture', payload);
  const bits = ['图片'];
  if (data.images) bits.push('已存 ' + data.images + ' 张');
  if (data.ocr_text) bits.push('OCR 出 ' + String(data.ocr_text).length + ' 字');
  if (text) bits.push('同时带上选区 ' + text.length + ' 字');
  reportSuccess(data, bits.join(' · '));
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
