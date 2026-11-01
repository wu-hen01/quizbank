'use strict';

/* ============================================================
 * 小工具
 * ========================================================== */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key.startsWith('on') && typeof value === 'function') {
        el.addEventListener(key.slice(2), value);
      } else el.setAttribute(key, value);
    }
  }
  for (const kid of kids.flat(3)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function normAns(value) {
  return String(value || '').replace(/\s+/g, '').toUpperCase().split('').sort().join('');
}
function isWrong(q) {
  return q.my_answer && q.correct_answer && normAns(q.my_answer) !== normAns(q.correct_answer);
}

let toastTimer = null;
function toast(message, isError) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 2600);
}

/* ============================================================
 * API
 * ========================================================== */
async function api(path, options = {}) {
  const opts = Object.assign({}, options);
  const headers = Object.assign({}, opts.headers || {});
  const token = localStorage.getItem('qb_token');
  if (token) headers['X-Token'] = token;

  let body = opts.body;
  if (body && typeof body !== 'string') {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }

  const res = await fetch(path, Object.assign({}, opts, { headers, body }));

  if (res.status === 401) {
    const entered = prompt('这个题库设置了访问 token，请输入：');
    if (entered && entered.trim()) {
      localStorage.setItem('qb_token', entered.trim());
      return api(path, options);
    }
    throw new Error('未授权');
  }

  const type = res.headers.get('content-type') || '';
  const data = type.includes('json') ? await res.json() : await res.text();
  if (!res.ok) {
    throw new Error((data && data.detail) || `请求失败 (${res.status})`);
  }
  return data;
}

/* ============================================================
 * 状态
 * ========================================================== */
const state = {
  tab: 'bank',
  questions: [],
  subjects: [],
  kps: [],
  filter: { q: '', subject: '', status: '', kp: '' },
  review: { items: [], index: 0, revealed: false, due: 0 },
  editing: null,
  quiz: { items: [], result: null, startedAt: 0, timer: null },
};

const TYPE_LABEL = {
  single: '单选', multiple: '多选', judge: '判断',
  fill: '填空', short: '简答', unknown: '题目',
};

const STATUS_LABEL = {
  ok: ['AI 解析', 'badge-ok'],
  pending: ['解析中…', 'badge-pending'],
  failed: ['解析失败', 'badge-failed'],
  skipped: ['非题目', 'badge-failed'],
  none: ['仅规则', 'badge-ghost'],
};

/* ============================================================
 * 题库
 * ========================================================== */
function statusBadge(status) {
  const [label, cls] = STATUS_LABEL[status] || STATUS_LABEL.none;
  return h('span', { class: 'badge ' + cls, text: label });
}

function previewOf(stem) {
  const line = String(stem || '').replace(/\s+/g, ' ').trim();
  return line.length > 90 ? line.slice(0, 90) + '…' : line;
}

/* 题干里的图（可能不止一张）：老数据只有 image_url，新数据是 image_urls */
function shotBlock(q) {
  const urls = (q && q.image_urls && q.image_urls.length)
    ? q.image_urls
    : (q && q.image_url ? [q.image_url] : []);
  if (!urls.length) return null;
  const box = h('div', { class: 'shots' });
  for (const url of urls) {
    box.appendChild(h('a', {
      class: 'shot', href: url, target: '_blank', rel: 'noreferrer', title: '点击看原图',
    }, h('img', { src: url, alt: '题目配图', loading: 'lazy' })));
  }
  return box;
}

function questionCard(q) {
  const card = h('article', { class: 'card qcard', title: '点一下展开/收起' });

  card.appendChild(h('div', { class: 'card-head' },
    h('span', { class: 'badge', text: TYPE_LABEL[q.qtype] || '题目' }),
    q.subject ? h('span', { class: 'badge badge-ghost', text: q.subject }) : null,
    q.chapter ? h('span', { class: 'badge badge-ghost', text: q.chapter }) : null,
    statusBadge(q.llm_status),
    h('span', { class: 'spacer' }),
    h('span', { class: 'chevron', text: '▾' })
  ));

  // 收起时只露一行题干，点开才显示题干全文、选项、答案、解析
  card.appendChild(h('div', { class: 'peek', text: previewOf(q.stem) }));

  const body = h('div', { class: 'card-body' });
  body.appendChild(h('div', { class: 'stem', text: q.stem }));

  const shots = shotBlock(q);
  if (shots) body.appendChild(shots);

  if (q.options && q.options.length) {
    const box = h('div', { class: 'options' });
    for (const opt of q.options) {
      box.appendChild(h('div', { class: 'option' },
        h('span', { class: 'key', text: opt.key || '·' }),
        h('span', { text: opt.text })
      ));
    }
    body.appendChild(box);
  }

  const answers = h('div', { class: 'answers' });
  if (q.my_answer) {
    answers.appendChild(h('span', {
      class: 'ans ans-mine' + (isWrong(q) ? ' wrong' : ''),
      text: '我的：' + q.my_answer,
    }));
  }
  if (q.correct_answer) {
    answers.appendChild(h('span', { class: 'ans ans-right', text: '正确：' + q.correct_answer }));
  }
  if (q.analysis) {
    answers.appendChild(h('div', { class: 'analysis', text: q.analysis }));
  }
  if (answers.childNodes.length) body.appendChild(answers);

  if (q.knowledge_points && q.knowledge_points.length) {
    const chips = h('div', { class: 'chips' });
    for (const kp of q.knowledge_points) {
      chips.appendChild(h('button', {
        class: 'chip', text: kp.name,
        onclick: () => filterByKp(kp.name),
      }));
    }
    body.appendChild(chips);
  }

  const foot = h('div', { class: 'card-foot' });
  if (q.source_url) {
    foot.appendChild(h('a', {
      class: 'link', href: q.source_url, target: '_blank', rel: 'noreferrer',
      text: q.source_site || '来源页面',
    }));
  }
  foot.appendChild(h('span', { class: 'muted', text: fmtDate(q.created_at) }));
  if (q.seen_count > 1) {
    foot.appendChild(h('span', { class: 'muted', text: `· 出现 ${q.seen_count} 次` }));
  }
  foot.appendChild(h('span', { class: 'spacer' }));
  foot.appendChild(h('button', { class: 'btn btn-sm', text: '编辑', onclick: () => openEdit(q) }));
  if (q.llm_status === 'pending' || q.llm_status === 'failed' || !q.analysis) {
    foot.appendChild(h('button', {
      class: 'btn btn-sm', text: '重新解析',
      onclick: () => reparse(q.id),
    }));
  }
  foot.appendChild(h('button', {
    class: 'btn btn-sm btn-danger', text: '删除',
    onclick: () => removeQuestion(q.id),
  }));
  body.appendChild(foot);
  card.appendChild(body);

  card.addEventListener('click', (event) => {
    // 点按钮、链接时不切换展开状态
    if (event.target.closest('button, a, input, textarea, select, img')) return;
    card.classList.toggle('is-open');
  });

  return card;
}

function emptyBlock(title, lines) {
  const box = h('div', { class: 'empty' });
  box.appendChild(h('strong', { text: title }));
  for (const line of lines) box.appendChild(h('div', { text: line }));
  return box;
}

async function loadBank(silent) {
  const params = new URLSearchParams();
  if (state.filter.q) params.set('q', state.filter.q);
  if (state.filter.subject) params.set('subject', state.filter.subject);
  if (state.filter.kp) params.set('kp', state.filter.kp);
  if (state.filter.status) params.set('status', state.filter.status);

  const data = await api('/api/questions?' + params.toString());
  state.questions = data.items;

  const host = document.getElementById('bank-list');
  clear(host);
  if (!data.items.length) {
    host.appendChild(emptyBlock('这里还没有题目', [
      '去学习通 / 任意网页选中一道题，右键 →「收录到 QuizBank」。',
      '也可以先点「刷新」看看是否已经在解析。',
    ]));
    return;
  }
  for (const q of data.items) host.appendChild(questionCard(q));

  if (!silent) console.debug('loaded', data.items.length);
  schedulePoll();
}

let pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  const pending = state.questions.some((q) => q.llm_status === 'pending');
  if (pending) pollTimer = setTimeout(() => loadBank(true).catch(() => {}), 2500);
}

function filterByKp(name) {
  state.filter.kp = name;
  state.filter.subject = '';
  const chip = document.getElementById('f-kp');
  chip.textContent = name + '  ✕';
  chip.classList.remove('hidden');
  switchTab('bank');
  loadBank();
}

async function removeQuestion(id) {
  if (!confirm('确定删除这道题？')) return;
  await api('/api/questions/' + id, { method: 'DELETE' });
  toast('已删除');
  await loadBank(true);
}

async function reparse(id) {
  try {
    await api(`/api/questions/${id}/reparse`, { method: 'POST' });
    toast('已提交重新解析');
    await loadBank(true);
  } catch (err) {
    toast(err.message, true);
  }
}

/* ============================================================
 * 学科
 * ========================================================== */
async function loadSubjects() {
  const data = await api('/api/subjects');
  state.subjects = data.items;

  const bankSel = document.getElementById('f-subject');
  const kpSel = document.getElementById('kp-subject');
  const quizSel = document.getElementById('q-subject');
  const currentBank = bankSel.value;
  const currentKp = kpSel.value;
  const currentQuiz = quizSel ? quizSel.value : '';

  for (const sel of [bankSel, kpSel, quizSel]) {
    if (!sel) continue;
    clear(sel);
    sel.appendChild(h('option', { value: '', text: '全部学科' }));
    for (const s of data.items) {
      sel.appendChild(h('option', {
        value: s.name,
        text: `${s.name}（${s.n}）`,
      }));
    }
  }
  bankSel.value = currentBank;
  kpSel.value = currentKp;
  if (quizSel) quizSel.value = currentQuiz;
}

/* ============================================================
 * 知识点
 * ========================================================== */
async function loadKps() {
  const subject = document.getElementById('kp-subject').value;
  const data = await api('/api/knowledge-points' + (subject ? '?subject=' + encodeURIComponent(subject) : ''));
  state.kps = data.items;

  const host = document.getElementById('kp-list');
  clear(host);

  if (!data.items.length) {
    host.appendChild(emptyBlock('还没有知识点', [
      '配置 LLM_API_KEY 后，AI 会自动给每道题打知识点标签。',
      '没有 AI 时，可以在题目「编辑」里手动填写知识点（逗号分隔）。',
    ]));
    return;
  }

  const groups = new Map();
  for (const kp of data.items) {
    const key = kp.subject || '未归类';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(kp);
  }

  for (const [subjectName, items] of groups) {
    host.appendChild(h('div', { class: 'kp-subject', text: subjectName }));
    const grid = h('div', { class: 'kp-grid' });
    for (const kp of items) {
      grid.appendChild(h('div', {
        class: 'kp-card',
        onclick: () => filterByKp(kp.name),
      },
        h('div', { class: 'name', text: kp.name }),
        h('div', { class: 'meta', text: `${kp.n} 道题` })
      ));
    }
    host.appendChild(grid);
  }
}

/* ============================================================
 * 复习
 * ========================================================== */
async function loadReview() {
  const data = await api('/api/review/queue?limit=50');
  state.review.items = data.items;
  state.review.index = 0;
  state.review.revealed = false;
  state.review.due = data.due;
  renderReview();
}

function renderReview() {
  const host = document.getElementById('review-host');
  clear(host);
  const { items, index } = state.review;

  if (!items.length) {
    host.appendChild(emptyBlock('今天没有到期的题目 🎉', [
      '收录新题后会自动进入复习队列。',
      '新题当天就会到期，可以先过一遍。',
    ]));
    return;
  }

  if (index >= items.length) {
    host.appendChild(emptyBlock('这一轮复习完成了 🎉', [
      `本轮共复习 ${items.length} 道题。`,
    ]));
    host.appendChild(h('div', { class: 'grade-row' },
      h('button', {
        class: 'btn btn-primary', text: '再加载一批',
        onclick: () => loadReview().catch((e) => toast(e.message, true)),
      })
    ));
    return;
  }

  const q = items[index];
  const card = h('div', { class: 'review-card' });

  card.appendChild(h('div', { class: 'review-meta' },
    h('div', { class: 'muted', text: `${index + 1} / ${items.length}${q.subject ? ' · ' + q.subject : ''}` }),
    h('div', { class: 'muted', text: q.chapter || '' })
  ));

  const progress = h('div', { class: 'progress' });
  progress.appendChild(h('i', { style: `width:${((index) / items.length) * 100}%` }));
  card.appendChild(progress);

  card.appendChild(h('div', { class: 'stem', text: q.stem }));

  const shots = shotBlock(q);
  if (shots) card.appendChild(shots);

  if (q.options && q.options.length) {
    const box = h('div', { class: 'options' });
    for (const opt of q.options) {
      box.appendChild(h('div', { class: 'option' },
        h('span', { class: 'key', text: opt.key || '·' }),
        h('span', { text: opt.text })
      ));
    }
    card.appendChild(box);
  }

  if (state.review.revealed) {
    const box = h('div', { class: 'reveal-box' });
    if (q.correct_answer) box.appendChild(h('div', { class: 'line', text: '正确答案：' + q.correct_answer }));
    if (q.my_answer) box.appendChild(h('div', { class: 'line', text: '我的答案：' + q.my_answer }));
    if (q.analysis) box.appendChild(h('div', { class: 'line', text: '解析：' + q.analysis }));
    if (!q.correct_answer && !q.analysis) box.appendChild(h('div', { class: 'line muted', text: '这道题没有记录答案或解析。' }));
    card.appendChild(box);

    card.appendChild(h('div', { class: 'grade-row' },
      h('button', { class: 'btn g1', text: '忘了', onclick: () => grade(1) }),
      h('button', { class: 'btn g2', text: '困难', onclick: () => grade(3) }),
      h('button', { class: 'btn g3', text: '一般', onclick: () => grade(4) }),
      h('button', { class: 'btn g4', text: '简单', onclick: () => grade(5) })
    ));
  } else {
    card.appendChild(h('div', { class: 'grade-row' },
      h('button', {
        class: 'btn btn-primary', text: '显示答案',
        onclick: () => { state.review.revealed = true; renderReview(); },
      })
    ));
  }

  host.appendChild(card);
}

async function grade(value) {
  const q = state.review.items[state.review.index];
  if (!q) return;
  try {
    const res = await api('/api/review/' + q.id, { method: 'POST', body: { grade: value } });
    toast('下次复习：' + res.label);
    state.review.index += 1;
    state.review.revealed = false;
    renderReview();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ============================================================
 * 统计
 * ========================================================== */
async function loadStats() {
  const data = await api('/api/stats');
  const host = document.getElementById('stats-host');
  clear(host);

  const grid = h('div', { class: 'stat-grid' });
  const cards = [
    ['收录题目', data.total],
    ['学科', data.subjects],
    ['知识点', data.knowledge_points],
    ['今日待复习', data.due],
    ['已作答', data.answered],
    ['正确率', data.accuracy === null ? '—' : data.accuracy + '%'],
  ];
  for (const [key, value] of cards) {
    grid.appendChild(h('div', { class: 'stat' },
      h('div', { class: 'k', text: key }),
      h('div', { class: 'v', text: String(value) })
    ));
  }
  host.appendChild(grid);

  host.appendChild(h('div', { class: 'section-title', text: '按学科分布' }));
  if (data.by_subject.length) {
    const table = h('table', { class: 'table' });
    table.appendChild(h('thead', null,
      h('tr', null, h('th', { text: '学科' }), h('th', { text: '题目数' }))
    ));
    const tbody = h('tbody');
    for (const s of data.by_subject) {
      tbody.appendChild(h('tr', null,
        h('td', { text: s.name }),
        h('td', { text: String(s.n) })
      ));
    }
    table.appendChild(tbody);
    host.appendChild(table);
  } else {
    host.appendChild(h('div', { class: 'muted', text: '还没有数据。' }));
  }

  host.appendChild(h('div', { class: 'section-title', text: '导出' }));
  const row = h('div', { class: 'export-row' });
  row.appendChild(h('a', {
    class: 'btn', href: exportUrl('anki'),
    text: '导出 Anki CSV（当前学科筛选）',
  }));
  row.appendChild(h('a', {
    class: 'btn', href: exportUrl('outline'),
    text: '导出知识要点 Markdown',
  }));
  host.appendChild(row);

  const hint = h('div', { class: 'muted', style: 'margin-top:10px;font-size:12px' });
  hint.textContent = 'Anki：桌面端「文件 → 导入」直接选 CSV，字段用逗号分隔。Markdown 适合贴进笔记软件。';
  host.appendChild(hint);
}

function exportUrl(kind) {
  const params = new URLSearchParams();
  if (state.filter.subject) params.set('subject', state.filter.subject);
  const token = localStorage.getItem('qb_token');
  if (token) params.set('token', token);
  const qs = params.toString();
  return `/api/export/${kind}` + (qs ? '?' + qs : '');
}

/* ============================================================
 * 编辑弹窗
 * ========================================================== */
function openEdit(q) {
  state.editing = q;
  const body = document.getElementById('modal-body');
  clear(body);

  const field = (label, control) => h('div', { class: 'field' },
    h('label', { text: label }), control);

  const stem = h('textarea', { rows: '4' });
  stem.value = q.stem || '';

  const options = h('textarea', { rows: '4' });
  options.value = (q.options || []).map((o) => `${o.key}. ${o.text}`).join('\n');

  const mine = h('input', { type: 'text' });
  mine.value = q.my_answer || '';

  const correct = h('input', { type: 'text' });
  correct.value = q.correct_answer || '';

  const analysis = h('textarea', { rows: '3' });
  analysis.value = q.analysis || '';

  const subject = h('input', { type: 'text', placeholder: '例如：高等数学' });
  subject.value = q.subject || '';

  const chapter = h('input', { type: 'text', placeholder: '例如：第三章 极限' });
  chapter.value = q.chapter || '';

  const kps = h('input', { type: 'text', placeholder: '多个用逗号分隔' });
  kps.value = (q.knowledge_points || []).map((k) => k.name).join('，');

  body.appendChild(field('题干', stem));
  body.appendChild(field('选项（一行一个，如 A. 内容）', options));
  body.appendChild(h('div', { class: 'field-row' },
    field('我的答案', mine),
    field('正确答案', correct)
  ));
  body.appendChild(field('解析', analysis));
  body.appendChild(h('div', { class: 'field-row' },
    field('学科', subject),
    field('章节', chapter)
  ));
  body.appendChild(field('知识点（逗号分隔）', kps));

  const save = h('button', { class: 'btn btn-primary', text: '保存' });
  save.addEventListener('click', async () => {
    const parsedOptions = [];
    for (const line of options.value.split('\n')) {
      const m = line.match(/^\s*([A-Ha-h])\s*[.、．)）:：]\s*(.+?)\s*$/);
      if (m) parsedOptions.push({ key: m[1].toUpperCase(), text: m[2] });
    }
    const payload = {
      stem: stem.value.trim(),
      options: parsedOptions,
      my_answer: mine.value.trim(),
      correct_answer: correct.value.trim(),
      analysis: analysis.value.trim(),
      subject: subject.value.trim(),
      chapter: chapter.value.trim(),
      knowledge_points: kps.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    };
    try {
      await api('/api/questions/' + q.id, { method: 'PATCH', body: payload });
      closeModal();
      toast('已保存');
      await Promise.all([loadBank(true), loadSubjects()]);
      if (state.tab === 'kp') await loadKps();
    } catch (err) {
      toast(err.message, true);
    }
  });

  body.appendChild(h('div', { style: 'display:flex;justify-content:flex-end;gap:8px;margin-top:6px' },
    h('button', { class: 'btn', text: '取消', onclick: closeModal }),
    save
  ));

  document.getElementById('modal').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
  state.editing = null;
}

/* ============================================================
 * 测验
 * ========================================================== */
function fmtSec(total) {
  const s = Math.max(0, Math.round(Number(total) || 0));
  const m = Math.floor(s / 60);
  return m ? `${m} 分 ${s % 60} 秒` : `${s} 秒`;
}

async function loadQuizPool() {
  const label = document.getElementById('quiz-pool');
  if (!label) return;
  const qs = new URLSearchParams();
  const subject = document.getElementById('q-subject').value;
  const kind = document.getElementById('q-kind').value;
  if (subject) qs.set('subject', subject);
  if (kind === 'objective') qs.set('kind', 'objective');
  try {
    const data = await api('/api/quiz/pool?' + qs.toString());
    label.textContent = data.available
      ? `这个范围里有 ${data.available} 道题`
      : '这个范围里没有题，先收录几道吧';
  } catch (err) {
    label.textContent = '　';
  }
}

function quizOptionInput(q, opt) {
  const type = q.qtype === 'multiple' ? 'checkbox' : 'radio';
  return h('label', { class: 'quiz-opt' },
    h('input', { type, name: 'q' + q.id, value: opt.key }),
    opt.key ? h('span', { class: 'key', text: opt.key }) : null,
    h('span', { text: opt.text })
  );
}

function renderQuiz() {
  const host = document.getElementById('quiz-host');
  clear(host);
  const items = state.quiz.items;

  host.appendChild(h('div', { class: 'quiz-head' },
    h('div', { class: 'muted', text: `共 ${items.length} 题` }),
    h('span', { class: 'spacer' }),
    h('span', { id: 'quiz-timer', class: 'pill pill-muted', text: '00:00' }),
    h('button', { class: 'btn btn-ghost', text: '放弃', onclick: () => resetQuiz() }),
    h('button', { class: 'btn btn-primary', text: '交卷', onclick: () => submitQuiz() })
  ));

  items.forEach((q, i) => {
    const card = h('article', { class: 'card quiz-q', 'data-id': String(q.id) });
    card.appendChild(h('div', { class: 'card-head' },
      h('span', { class: 'badge', text: `第 ${i + 1} 题` }),
      h('span', { class: 'badge badge-ghost', text: TYPE_LABEL[q.qtype] || '题目' }),
      q.subject ? h('span', { class: 'badge badge-ghost', text: q.subject }) : null,
      q.auto_graded ? null : h('span', { class: 'badge badge-ghost', text: '交卷后自评' })
    ));
    card.appendChild(h('div', { class: 'stem', text: q.stem }));

    const shots = shotBlock(q);
    if (shots) card.appendChild(shots);

    if (q.options && q.options.length) {
      const box = h('div', { class: 'quiz-opts' });
      for (const opt of q.options) box.appendChild(quizOptionInput(q, opt));
      card.appendChild(box);
    } else if (q.qtype === 'judge') {
      const box = h('div', { class: 'quiz-opts' });
      for (const v of ['正确', '错误']) {
        box.appendChild(h('label', { class: 'quiz-opt' },
          h('input', { type: 'radio', name: 'q' + q.id, value: v }),
          h('span', { text: v })
        ));
      }
      card.appendChild(box);
    } else {
      card.appendChild(h('textarea', {
        class: 'quiz-input', rows: '3', placeholder: '写下你的答案（简答/填空不会自动判分）',
      }));
    }

    host.appendChild(card);
  });

  host.appendChild(h('div', { class: 'quiz-foot' },
    h('button', { class: 'btn', text: '放弃', onclick: () => resetQuiz() }),
    h('button', { class: 'btn btn-primary', text: '交卷', onclick: () => submitQuiz() })
  ));

  startQuizTimer();
}

function startQuizTimer() {
  stopQuizTimer();
  const tick = () => {
    const el = document.getElementById('quiz-timer');
    if (!el || !state.quiz.startedAt) return;
    const s = Math.floor((Date.now() - state.quiz.startedAt) / 1000);
    el.textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  };
  tick();
  state.quiz.timer = setInterval(tick, 1000);
}

function stopQuizTimer() {
  if (state.quiz.timer) {
    clearInterval(state.quiz.timer);
    state.quiz.timer = null;
  }
}

function collectQuizAnswers() {
  const out = [];
  for (const card of document.querySelectorAll('.quiz-q')) {
    const id = Number(card.dataset.id);
    const picked = Array.from(card.querySelectorAll('input:checked')).map((el) => el.value);
    const area = card.querySelector('textarea');
    const answer = picked.length ? picked.sort().join('') : (area ? area.value.trim() : '');
    out.push({ id, answer });
  }
  return out;
}

async function startQuiz() {
  const payload = {
    subject: document.getElementById('q-subject').value,
    size: Number(document.getElementById('q-size').value) || 10,
    kind: document.getElementById('q-kind').value,
    prefer: document.getElementById('q-prefer').value,
  };
  try {
    const data = await api('/api/quiz/start', { method: 'POST', body: payload });
    if (!data.count) {
      toast('这个范围里没有可用的题', true);
      return;
    }
    if (data.count < payload.size) toast(`只抽到 ${data.count} 道题`);
    state.quiz = {
      items: data.items, result: null, startedAt: Date.now(), timer: null,
    };
    document.getElementById('quiz-setup').classList.add('hidden');
    document.getElementById('quiz-host').classList.remove('hidden');
    renderQuiz();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    toast(err.message, true);
  }
}

async function submitQuiz() {
  const answers = collectQuizAnswers();
  const blank = answers.filter((a) => !a.answer).length;
  if (blank && !window.confirm(`还有 ${blank} 道没答，确定交卷？`)) return;

  stopQuizTimer();
  const seconds = Math.floor((Date.now() - state.quiz.startedAt) / 1000);
  try {
    const data = await api('/api/quiz/submit', { method: 'POST', body: { answers, seconds } });
    state.quiz.result = data;
    renderQuizResult();
  } catch (err) {
    toast(err.message, true);
    startQuizTimer();
  }
}

function recountQuiz(items, seconds) {
  const graded = items.filter((it) => it.graded);
  const right = graded.filter((it) => it.correct === true);
  return {
    total: items.length,
    graded: graded.length,
    right: right.length,
    wrong: graded.length - right.length,
    ungraded: items.length - graded.length,
    accuracy: graded.length ? Math.round((right.length / graded.length) * 10000) / 10000 : 0,
    score: graded.length ? Math.round((right.length / graded.length) * 100) : 0,
    seconds,
  };
}

async function markQuizItem(id, correct) {
  try {
    await api('/api/quiz/mark', { method: 'POST', body: { items: [{ id, correct }] } });
    const it = state.quiz.result.items.find((x) => x.id === id);
    if (it) {
      it.graded = true;
      it.correct = correct;
    }
    state.quiz.result.score = recountQuiz(
      state.quiz.result.items, state.quiz.result.seconds
    );
    renderQuizResult();
  } catch (err) {
    toast(err.message, true);
  }
}

function renderQuizResult() {
  const host = document.getElementById('quiz-host');
  const data = state.quiz.result;
  const s = data.score;
  clear(host);

  host.appendChild(h('div', { class: 'card score-card' },
    h('div', { class: 'score-num' + (s.graded ? (s.score >= 60 ? '' : ' is-low') : ' is-none'),
      text: s.graded ? String(s.score) : '—' }),
    h('div', { class: 'score-info' },
      h('div', { class: 'score-line', text: s.graded
        ? `客观题 ${s.right} / ${s.graded} 对（正确率 ${Math.round(s.accuracy * 100)}%）`
        : '这张卷子没有可自动判分的题' }),
      h('div', { class: 'muted', text: `共 ${s.total} 题${s.ungraded ? ` · ${s.ungraded} 题待自评` : ''} · 用时 ${fmtSec(data.seconds)}` })
    ),
    h('span', { class: 'spacer' }),
    h('button', { class: 'btn', text: '再来一张', onclick: () => resetQuiz() })
  ));

  const wrongCount = data.items.filter((it) => it.correct === false).length;
  if (wrongCount) {
    host.appendChild(h('div', { class: 'quiz-foot' },
      h('span', { class: 'muted', text: `错了 ${wrongCount} 题，已经排进今天的复习队列` }),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', text: `只练这 ${wrongCount} 道错题`, onclick: () => retryWrong() })
    ));
  }

  data.items.forEach((it, i) => {
    const card = h('article', { class: 'card quiz-q is-marked' });
    const verdict = it.graded
      ? (it.correct
        ? h('span', { class: 'badge badge-ok', text: '✔ 答对' })
        : h('span', { class: 'badge badge-failed', text: '✘ 答错' }))
      : h('span', { class: 'badge badge-pending', text: '待自评' });

    card.appendChild(h('div', { class: 'card-head' },
      h('span', { class: 'badge', text: `第 ${i + 1} 题` }),
      h('span', { class: 'badge badge-ghost', text: TYPE_LABEL[it.qtype] || '题目' }),
      verdict,
      h('span', { class: 'spacer' })
    ));
    card.appendChild(h('div', { class: 'stem', text: it.stem }));

    const shots = shotBlock(it);
    if (shots) card.appendChild(shots);

    if (it.options && it.options.length) {
      const box = h('div', { class: 'options' });
      for (const opt of it.options) {
        const mineKeys = String(it.my_answer || '').toUpperCase();
        const cls = 'option'
          + (mineKeys.includes(opt.key) ? ' picked' : '')
          + (it.graded && String(it.correct_answer || '').toUpperCase().includes(opt.key) ? ' right' : '');
        box.appendChild(h('div', { class: cls },
          h('span', { class: 'key', text: opt.key || '·' }),
          h('span', { text: opt.text })
        ));
      }
      card.appendChild(box);
    }

    const box = h('div', { class: 'reveal-box' });
    box.appendChild(h('div', { class: 'line', text: '我的答案：' + (it.my_answer || '（空）') }));
    if (it.correct_answer) {
      box.appendChild(h('div', { class: 'line', text: '正确答案：' + it.correct_answer }));
    }
    if (it.analysis) box.appendChild(h('div', { class: 'line', text: '解析：' + it.analysis }));
    if (!it.correct_answer && !it.analysis) {
      box.appendChild(h('div', { class: 'line muted', text: '这道题没有记录答案或解析。' }));
    }
    card.appendChild(box);

    if (!it.graded) {
      card.appendChild(h('div', { class: 'grade-row' },
        h('span', { class: 'muted', text: '自己判一下：' }),
        h('button', { class: 'btn g3', text: '我做对了', onclick: () => markQuizItem(it.id, true) }),
        h('button', { class: 'btn g1', text: '我做错了', onclick: () => markQuizItem(it.id, false) })
      ));
    }

    host.appendChild(card);
  });
}

function retryWrong() {
  const wrong = state.quiz.result.items.filter((it) => it.correct === false);
  if (!wrong.length) {
    toast('这张卷子没有错题');
    return;
  }
  state.quiz = {
    items: wrong.map((it) => ({
      id: it.id, stem: it.stem, qtype: it.qtype, options: it.options,
      image_url: it.image_url, subject: '', chapter: '',
      knowledge_points: [], auto_graded: !!it.correct_answer,
    })),
    result: null, startedAt: Date.now(), timer: null,
  };
  renderQuiz();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function resetQuiz() {
  stopQuizTimer();
  state.quiz = { items: [], result: null, startedAt: 0, timer: null };
  document.getElementById('quiz-host').classList.add('hidden');
  document.getElementById('quiz-setup').classList.remove('hidden');
  loadQuizPool().catch(() => {});
}

/* ============================================================
 * 标签切换 & 初始化
 * ========================================================== */
function switchTab(name) {
  state.tab = name;
  for (const btn of document.querySelectorAll('.tab')) {
    btn.classList.toggle('active', btn.dataset.tab === name);
  }
  for (const view of document.querySelectorAll('.view')) {
    view.classList.toggle('active', view.id === 'view-' + name);
  }
  if (name === 'bank') loadBank(true).catch((e) => toast(e.message, true));
  if (name === 'kp') loadKps().catch((e) => toast(e.message, true));
  if (name === 'review') loadReview().catch((e) => toast(e.message, true));
  if (name === 'quiz') loadQuizPool().catch((e) => toast(e.message, true));
  if (name === 'stats') loadStats().catch((e) => toast(e.message, true));
}

async function loadHealth() {
  try {
    const data = await api('/api/health');
    const pill = document.getElementById('llm-pill');
    if (data.llm_enabled) {
      pill.className = 'pill pill-ok';
      pill.textContent = 'AI 已启用 · ' + data.model;
    } else {
      pill.className = 'pill pill-warn';
      pill.textContent = '未配置 AI（仅规则解析）';
    }
  } catch (err) {
    const pill = document.getElementById('llm-pill');
    pill.className = 'pill pill-warn';
    pill.textContent = '服务未连接';
  }
}

function bindEvents() {
  for (const btn of document.querySelectorAll('.tab')) {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  }

  document.getElementById('btn-refresh').addEventListener('click', async () => {
    await Promise.all([loadHealth(), loadSubjects()]);
    if (state.tab === 'bank') await loadBank(true);
    if (state.tab === 'kp') await loadKps();
    if (state.tab === 'stats') await loadStats();
    toast('已刷新');
  });

  const search = document.getElementById('f-q');
  let searchTimer = null;
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.filter.q = search.value.trim();
      loadBank(true).catch((e) => toast(e.message, true));
    }, 250);
  });

  document.getElementById('f-subject').addEventListener('change', (e) => {
    state.filter.subject = e.target.value;
    loadBank(true).catch((err) => toast(err.message, true));
  });

  document.getElementById('f-status').addEventListener('change', (e) => {
    state.filter.status = e.target.value;
    loadBank(true).catch((err) => toast(err.message, true));
  });

  document.getElementById('f-kp').addEventListener('click', () => {
    state.filter.kp = '';
    document.getElementById('f-kp').classList.add('hidden');
    loadBank(true).catch((e) => toast(e.message, true));
  });

  document.getElementById('kp-subject').addEventListener('change', () => {
    loadKps().catch((e) => toast(e.message, true));
  });

  document.getElementById('quiz-start').addEventListener('click', () => startQuiz());
  for (const id of ['q-subject', 'q-kind']) {
    document.getElementById(id).addEventListener('change', () => {
      loadQuizPool().catch(() => {});
    });
  }

  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
  });

  window.addEventListener('focus', () => {
    if (state.tab === 'bank') loadBank(true).catch(() => {});
  });
}

async function init() {
  bindEvents();
  await loadHealth();
  try {
    await loadSubjects();
    await loadBank(true);
  } catch (err) {
    toast(err.message, true);
  }
}

document.addEventListener('DOMContentLoaded', init);
