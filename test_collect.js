// 临时探针：在没有 Chrome 的环境里用假 DOM 验证 collectFromPage 的取图逻辑。
const fs = require('fs');

const src = fs.readFileSync('extension/background.js', 'utf8');
const start = src.indexOf('async function collectFromPage');
if (start < 0) throw new Error('collectFromPage not found');
const end = src.indexOf('\n}', start);
const fnSrc = src.slice(start, end + 2);

function makeImg(src_, cls, w, h) {
  return {
    src: src_,
    currentSrc: src_,
    className: cls || '',
    id: '',
    alt: '',
    getBoundingClientRect: () => ({ width: w, height: h }),
  };
}

function makeEl(imgs, text, parent) {
  return {
    nodeType: 1,
    innerText: text,
    parentElement: parent || null,
    querySelectorAll: (sel) => (sel === 'img' ? imgs : []),
  };
}

async function run(label, setup, expect) {
  const factory = new Function(
    'window', 'document', 'fetch', 'FileReader',
    'return (' + fnSrc + ');'
  );
  const ctx = setup();
  const fn = factory(ctx.window, ctx.document, ctx.fetch, ctx.FileReader);
  const out = await fn(20000, ctx.onlySrc || '');
  const ok = expect(out, ctx);
  console.log((ok ? '  PASS  ' : '  FAIL  ') + label);
  if (!ok) {
    console.log('        out=', JSON.stringify(out).slice(0, 400));
  }
  return ok;
}

function baseCtx(imgs, containerText) {
  const container = makeEl(imgs, containerText, {
    nodeType: 1,
    innerText: 'outer',
    parentElement: null,
    querySelectorAll: () => [],
  });
  const range = {
    intersectsNode: () => false,
    commonAncestorContainer: container,
  };
  const win = {
    getSelection: () => ({ rangeCount: 1, isCollapsed: false, getRangeAt: () => range }),
    getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' }),
  };
  const doc = { images: imgs, body: { nodeType: 1 }, documentElement: { nodeType: 1 }, createElement: () => ({ getContext: () => null }) };
  const fetchStub = async () => ({ ok: true, blob: async () => ({ type: 'image/png' }) });
  class FR {
    readAsDataURL() {
      this.result = 'data:image/png;base64,QUJD';
      setTimeout(() => this.onload && this.onload(), 0);
    }
  }
  return { window: win, document: doc, fetch: fetchStub, FileReader: FR, container };
}

(async () => {
  let pass = 0, total = 0;
  const t = async (label, setup, expect) => {
    total++;
    if (await run(label, setup, expect)) pass++;
  };

  // 1) 只选文字，题干容器里有 2 张可用图 → 都取走
  await t('选中文字 + 容器内 2 张图', () => {
    const imgs = [makeImg('https://a.test/1.png', 'q-img', 300, 200), makeImg('https://a.test/2.png', 'q-img', 120, 90)];
    return baseCtx(imgs, '题干文字');
  }, (out) => out.images.length === 2 && out.images.every((s) => s.startsWith('data:image/')));

  // 2) 装饰图（logo/1x1/占位）要被过滤
  await t('过滤装饰图', () => {
    const imgs = [
      makeImg('https://a.test/logo.png', 'logo', 300, 200),
      makeImg('https://a.test/x.png', '', 1, 1),
      makeImg('https://a.test/real.png', 'q-img', 300, 200),
      makeImg('data:image/svg+xml;base64,AAA', '', 300, 200),
    ];
    return baseCtx(imgs, '题干');
  }, (out) => out.images.length === 1 && out.images[0].startsWith('data:image/png'));

  // 3) 超过 6 张只取前 6
  await t('最多 6 张', () => {
    const imgs = [];
    for (let i = 0; i < 10; i++) imgs.push(makeImg('https://a.test/' + i + '.png', 'q', 300, 200));
    return baseCtx(imgs, '题干');
  }, (out) => out.images.length === 6);
  // 4) dataURL 转不出来 → 退回绝对 URL（交给服务端下载）
  await t('转不了就交绝对 URL', () => {
    const imgs = [makeImg('https://a.test/cdn.png', 'q', 300, 200)];
    const ctx = baseCtx(imgs, '题干');
    ctx.fetch = async () => {
      throw new Error('CORS');
    };
    ctx.document.createElement = () => ({ getContext: () => { throw new Error('tainted'); } });
    return ctx;
  }, (out) => out.images.length === 1 && out.images[0] === 'https://a.test/cdn.png');

  // 5) 右键单张图模式：只转这一张
  await t('onlySrc 只转指定图', () => {
    const imgs = [makeImg('https://a.test/x.png', 'q', 300, 200), makeImg('https://a.test/y.png', 'q', 300, 200)];
    const ctx = baseCtx(imgs, '题干');
    ctx.onlySrc = 'https://a.test/y.png';
    return ctx;
  }, (out) => out.count === 1 && out.images.length === 1);

  // 6) 页面抛错时返回空结果而不是 reject
  await t('页面异常时安全返回', () => {
    const ctx = baseCtx([], '题干');
    ctx.document.images = null;
    ctx.window.getSelection = () => {
      throw new Error('boom');
    };
    return ctx;
  }, (out) => out.images.length === 0 && typeof out.text === 'string');

  console.log('\n结果：' + pass + ' 通过 / ' + (total - pass) + ' 失败');
  process.exit(pass === total ? 0 : 1);
})();
