# QuizBank · 本地错题题库

把你在学习通、智慧树或任意网页上做过的题，通过浏览器右键一键收进本地题库；
自动拆出题干 / 选项 / 答案 / 解析，按学科归类，提取知识点，并按间隔重复安排复习。

数据全部存在本机的一个 SQLite 文件里，不上传任何服务器（除非你自己配置了云端大模型）。

> 定位：**个人错题本 + 复习系统**。它帮你把"做过的题"沉淀成知识要点，
> 不参与考试作答，也不批量爬取平台题库。

---

## 1. 目录结构

```
quizbank/
├─ app/
│  ├─ config.py      配置（读 .env / 环境变量）
│  ├─ db.py          SQLite 建表与连接（含自动加列的迁移）
│  ├─ parse.py       规则解析：题干、选项、答案
│  ├─ llm.py         调用 OpenAI 兼容接口做结构化
│  ├─ ocr.py         截图 OCR（调用 Windows 自带识别引擎）
│  ├─ windows_ocr.ps1  OCR 的 PowerShell 实现
│  ├─ review.py      间隔重复调度（SM-2）
│  ├─ quiz.py        模拟测验：抽题条件与判分规则
│  ├─ exporter.py    导出 Anki CSV / Markdown 提纲
│  └─ main.py        FastAPI 路由
├─ static/           网页界面（原生 JS，无需构建）
├─ extension/        Chrome 扩展（MV3）
├─ data/             运行后自动生成 quizbank.db 和 images/ 截图
├─ run.py            启动入口
├─ start.bat         Windows 一键启动
├─ resplit.py        把「整页被当成一道题」的旧记录拆回多道题
├─ test_e2e.py       接口冒烟测试（用临时库，不动真实数据）
├─ test_llm.py       AI 结构化测试（用本地假接口，不花 token）
├─ test_collect.js   用假 DOM 测扩展的「取文字 + 取图」逻辑（需要 Node）
├─ pack.py           打发行包：python pack.py → dist/quizbank-<版本>.zip
└─ .env.example      配置模板
```

## 2. 安装与启动

需要 Python 3.9+。

```bat
cd D:\1\quizbank
python -m pip install -r requirements.txt
copy .env.example .env      :: 然后编辑 .env，填入 LLM_API_KEY
python run.py
```

或者直接双击 `start.bat`（首次会自动装依赖）。

看到 `Uvicorn running on http://127.0.0.1:8765` 后，浏览器打开
**http://127.0.0.1:8765**。

停止服务：在窗口里按 `Ctrl + C`。

## 3. 配置大模型（可选，但强烈建议）

编辑 `.env`：

```ini
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-你的key
```

换成 OpenAI 只需要改这两行：

```ini
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

用本地 Ollama（数据不出本机）：

```ini
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_API_KEY=ollama
```

**不填也能用**：会退回规则解析，题干/选项/答案基本能抽出来，
只是没有学科归类、知识点和解析，需要手动在「编辑」里补。

改完 `.env` 必须重启服务。

## 4. 安装浏览器扩展

1. Chrome / Edge 打开 `chrome://extensions/`（Edge 是 `edge://extensions/`）
2. 右上角打开「开发者模式」
3. 点「加载已解压的扩展程序」，选择 `D:\1\quizbank\extension` 目录
4. 点击工具栏的 QuizBank 图标 → 打开设置页，确认「服务地址」是
   `http://127.0.0.1:8765`，点「测试连接」应显示连接成功

## 5. 使用流程

### 文字收录

1. 在学习通里打开某道题的**解析页 / 已做过的试卷详情**
2. 用鼠标**选中**题目（题干 + 选项 + 答案/解析）
3. **右键 →「收录到 QuizBank」**
4. 桌面右下角弹出「已收录 ✓」，网页端按「刷新」即可看到
5. AI 会异步补全学科、章节、知识点；右上角状态会从「解析中…」变成「AI 解析」

抓取是按这个顺序取文本的：**Chrome 给的选中文字 → 各 iframe 里的选区 → 页面主内容区**。
所以只要选中了题目，就只会收录选中的部分，不会是整页；只有什么都没选时才会退回整页
（此时通知里会写「整页 N 字」，而不是「选区 N 字」）。

**一页多题也能一次收完**：如果整页文字是「1. … 2. … 3. …」这样从 1 连续编号的，
服务端会按题号切成多道题分别收录，通知显示「已收录 5 道题」。
编号不连续（比如解析里顺手写的 1. 2.）不会触发切分，避免把一道题拆碎。

重复收同一道题不会新增记录，只会累加「出现次数」并补全缺失的答案。

### 题干里带图片

很多题是「文字 + 一张图」的组合（几何图、电路图、流程图、二叉树）。不用分开操作：

1. 照常**选中**题目的文字部分
2. 右键 →「收录到 QuizBank（文字 + 题干里的图）」
3. 扩展会同时取走选区文字和**题干附近最多 6 张图**，一次提交
4. 卡片上文字和图并排显示，点图可以看原图

取图规则：优先在页面内把图转成 `dataURL` 直接传给本地服务（带 cookie，所以需要
登录的图床也能拿到）；转不成的（跨域、防盗链）就把图片地址交给服务端下载。
单题最多存 6 张图；装饰性图片（图标、头像、占位图、1×1 像素）会被过滤掉。

只想收图、不要文字（比如整道题就是一张图）时，仍然可以右键图片单独收录（见下一节）。
同一道题重复收录时，新图会**追加**到已有记录上，不会覆盖。

如果早期版本把整页收成了一条记录，可以拆回来：

```bat
python resplit.py 9 --dry   :: 先看会切成几段，不动数据库
python resplit.py 9         :: 真的拆，原记录删除
```

### 截图收录（OCR）

题目里有公式、图片，或者页面是 canvas / iframe 导致选不中文字时用这个：

1. 右键**题目里的图片** →「收录这张图片到 QuizBank（OCR）」
2. 服务端用 Windows 自带 OCR 识别出文字，再走和文字收录一样的解析
3. 原图会存到 `data/images/`，卡片上能看到截图，点开是大图

也可以不用扩展，直接把截图拖进任何能发 POST 的地方：

```bash
curl -X POST http://127.0.0.1:8765/api/capture/image \
  -H "Content-Type: application/json" \
  -d "{\"image\":\"data:image/png;base64,....\"}"
```

OCR 的要点：

- 用的是 Windows 10/11 自带的识别引擎，**不用安装 tesseract，也不用联网**
- 中文语言包一般是系统自带的；`/api/health` 里的 `ocr_available` 能看到是否可用
- Windows 的中文识别会在每个汉字之间插空格，`app/ocr.py` 里的 `clean_text()`
  会把这些空格合并掉，再交给解析器
- 识别不准时，可以在网页端点「编辑」直接改题干/选项，比重新截图快

## 6. 界面说明

| 标签 | 能做什么 |
|------|----------|
| 题库 | 搜索、按学科/状态过滤，点知识点标签反查题目，编辑/删除/重新解析 |
| 知识点 | 按学科分组列出知识点及题量，点进去看对应该知识点的所有题 |
| 复习 | 到期队列，卡片式自测，四档评分（忘了/困难/一般/简单）驱动 SM-2 调度 |
| 测验 | 从题库随机组一张卷子，限时作答，交卷自动判客观题 |
| 统计/导出 | 题量、正确率、学科分布；导出 Anki CSV 或 Markdown 知识提纲 |

题库里的卡片**默认只露一行题干**，点一下展开完整题干、选项、答案、解析和操作按钮
（点按钮/链接/图片不会误触发折叠）。题干带图时，展开后图片按行并排显示，
点任意一张看原图；复习页、测验答题页、测验结果页同样会显示配图。
复习页始终是「先看题 → 点显示答案」的节奏。

**没有解析的题会有 AI 讲解**：原文里没写解析时，AI 会自己写一段解题要点，
以「（AI 讲解）」开头，方便和原文解析区分。想重新生成，点那道题的「重新解析」。

导出 Anki：桌面 Anki「文件 → 导入」，选 `quizbank.csv`，
字段分隔符用逗号，第 3 列自动变成标签。

### 模拟测验怎么用

1. 点「测验」标签，选范围（学科）、题量（5/10/20/30）、题型和抽题偏好；
   右上角会显示这个范围里有多少题可用。
2. 点「开始测验」。答题页单选是圆点、多选是方框、判断题是「正确/错误」两个选项，
   填空题和简答题是文本框；右上角有计时器。
3. 交卷后：单选/多选/判断题自动判分并显示得分，**答错的题自动排进今天的复习队列**；
   简答和填空不自动判分，会显示「我做对了 / 我做错了」两个按钮让你自评。
4. 结果页底部可以「只练这 N 道错题」再刷一遍，右边「再来一张」重新组卷。

抽题偏好说明：

| 选项 | 含义 |
|------|------|
| 随机 | 每条题等概率 |
| 优先最近做错的 | 按最近一次作答结果排序，错的先来，没做过的排最后 |
| 优先没做过的 | 按作答次数升序，零次的最先 |

判分规则（`app/quiz.py`）：选择题先把答案规范化（去空白/标点、字母排序，多选不看顺序）；
判断题认得「正确/错误/对/错/√/×/T/F」以及 `A. 正确 / B. 错误` 这种选项字母写法；
缺正确答案的题不判分，走自评。

## 7. 数据结构（想自己写脚本时看）

```
questions      题目主表，stem_hash 做去重键
subjects       学科
knowledge_points  知识点（可挂 parent_id 做树）
question_kp    题目 ↔ 知识点
attempts       作答记录（收录时若带"我的答案"就记一条）
reviews        复习调度状态（due_at / interval / ease / reps）
```

主要接口：

```
POST   /api/capture                     收录文字 + 题干里的图（扩展调用）
POST   /api/capture/image               收录图片：OCR 后入库
GET    /api/questions?q=&subject=&kp=   列表
PATCH  /api/questions/{id}              改题
DELETE /api/questions/{id}              删题
POST   /api/questions/{id}/reparse      重新 AI 解析
GET    /api/subjects                    学科
GET    /api/knowledge-points            知识点
POST   /api/knowledge-points/merge      合并同名知识点
GET    /api/review/queue                今日到期
POST   /api/review/{id}  {grade:0-5}    提交复习结果
GET    /api/quiz/pool?subject=&kind=    该范围可用题量
POST   /api/quiz/start                  组卷（不含答案）
POST   /api/quiz/submit                 交卷判分
POST   /api/quiz/mark                   简答/填空自评
GET    /api/stats                       统计
GET    /api/export/anki?subject=        Anki CSV
GET    /api/export/outline?subject=     Markdown 提纲
GET    /images/{文件名}                  题目截图
```

`POST /api/quiz/start` 的请求体：

```json
{ "subject": "人工智能", "kp": "", "size": 10,
  "kind": "all", "prefer": "random" }
```

`kind` 取 `all` / `objective`（只考能自动判分的题）；`prefer` 取 `random` / `wrong` / `unseen`。
返回的题面**故意不含 `correct_answer` 和 `analysis`**，交卷时由 `/api/quiz/submit` 一并返回：

```json
{ "answers": [ { "id": 20, "answer": "B" }, { "id": 21, "answer": "" } ],
  "seconds": 93 }
```

`POST /api/capture` 的请求体（除 `text` 外都可省）：

```json
{ "text": "选中的题目文字",
  "images": ["data:image/png;base64,...", "https://site.com/q1.png"],
  "url": "页面地址", "title": "页面标题", "site": "站点名",
  "ocr": false, "keep_image": true, "whole_page": false, "selection_len": 320 }
```

`images` 每项可以是 `dataURL`/裸 base64，也可以是 http(s) 地址（由服务端下载），
最多 6 张。`ocr: true` 时即使有文字也会额外跑一遍识别。返回里的 `images` 是实际存下的张数，
失败的单张会放进 `image_errors`，不影响文字入库。

`POST /api/capture/image` 的请求体（三种来源任选其一）：

```json
{ "image": "data:image/png;base64,..." }         // dataURL
{ "image_url": "https://.../q1.png" }            // 服务端下载
{ "image": "...", "text": "手动改好的题干" }      // text 优先于 OCR 结果
```

自带接口文档：<http://127.0.0.1:8765/api/docs>

## 8. 安全与边界

- 服务只监听 `127.0.0.1`，外部访问不到。**不要改成 `0.0.0.0`**，除非同时设置
  `QUIZBANK_TOKEN`，否则同一局域网的任何人都能读写你的题库。
- **跨域默认只放行本机页面和浏览器扩展**（`http://127.0.0.1:8765`、
  `http://localhost:8765`、`chrome-extension://…`）。这样别的网页即使用 JS 去请求
  `http://127.0.0.1:8765`，浏览器也不许它读响应，防止被恶意页面偷题库。
  需要放开时在 `.env` 里写 `QUIZBANK_CORS=*` 或逗号分隔的来源（不建议）。
- 想再加一层保护，在 `.env` 里设置 `QUIZBANK_TOKEN=一串随机字符`，
  然后在扩展设置页填同样的值。网页端首次访问会提示输入。
- **服务端下载远程图片只允许公网地址**：本机、局域网、`169.254.169.254`（云元数据）
  一律拒绝，重定向也逐跳校验，并会按文件头确认真的是图片。所以 `image_url` 这条
  兜底路径不会被当成读取内网的跳板；内网里的课件图片请依赖扩展在页面内转成
  图片数据（正常情况下走的就是这条）。
- 截图目录 `data/images/` 有总量上限（500 MB），超了就不再保存新图，避免被刷满磁盘。
- 数据库就是 `data/quizbank.db`，备份/迁移直接拷这个文件。
- 本仓库**不含任何密钥或个人数据**：`.env`（存 API key）和 `data/`（你的题库）
  都在 `.gitignore` 里。发布出去的压缩包也是同样规则。
- **请只收录自己做过的题用于复习**。批量爬取平台题库、或在考试中用题库检索答案，
  性质完全不同，也违反平台协议。

## 9. 常见问题

**右键没有「收录到 QuizBank」？**
重新加载扩展：`chrome://extensions/` → 点扩展的刷新按钮。

**题干里的图没跟着收进来？**
先确认扩展是最新的（`chrome://extensions/` 点刷新），菜单标题应该叫
「收录到 QuizBank（文字 + 题干里的图）」。取图只找选区**附近**的图片：
从选区所在元素向上最多 8 层，遇到第一段带图的容器就取里面的图（最多 6 张）。
如果图离题目很远（比如整页顶部的大图），单独右键那张图收录更稳。
存不下的原因会出现在通知里（`图片 N 张失败`），也可以在网页端「编辑」里补。

**扩展提示「Failed to fetch」？**
服务没启动，或端口不是 8765。确认 `python run.py` 的窗口还开着。

**AI 状态一直「解析中…」？**
检查 `.env` 里的 key 和 `LLM_BASE_URL`；设置页点「测试连接」看返回。
超时后状态会变成「解析失败」，但规则解析的内容已经存下来了，不会丢题。
接口遇到限流/超时会自动退避重试 3 次，仍失败才标「解析失败」，之后可以点「重新解析」。

**学习通页面是 iframe 嵌套的？**
选中文字后右键即可。扩展优先用 Chrome 直接给出的选中文字（`info.selectionText`），
它在 iframe、富文本编辑器里都是可靠的；拿不到才回退到各 frame 的 `getSelection()`。

**一页有好几道题，只收了一道 / 收成了整页？**
确认是「选中」了再右键。整页文字若是「1. 2. 3.」连续编号，服务端会自动拆成多道题。
旧版本收坏的记录用 `python resplit.py <id>` 拆回来。

**题一直停在「解析中…」/ 好像不自动解析了？**
先看服务还在不在：浏览器打开 <http://127.0.0.1:8765/api/health>，
打不开就说明 `run.py` 的窗口被关了，重启一下就好。
启动时会自动把上次没解析完的题重新排进队列（不用手点「重新解析」）。

**想让某道题重新解析？**
网页端点开那道题 →「重新解析」；或者把 `llm_status` 改成 pending 后重启服务。

**提示「连不上本地服务」？**
扩展里显示的是它尝试访问的地址，通常是 `http://127.0.0.1:8765`。
先双击 `start.bat`，看到 `Uvicorn running on http://127.0.0.1:8765` 再去收录。

**想换端口？**
改 `.env` 的 `QUIZBANK_PORT`，同时把扩展设置里的地址一起改。

**OCR 说「系统没有安装中文 OCR 语言包」？**
Windows 设置 → 时间和语言 → 语言 → 添加「中文(简体，中国)」，
并在「语言功能」里勾上基本输入/OCR 相关组件。也可以把 `.env` 的
`OCR_LANG` 改成 `en-US` 只识别英文。

**右键图片没有「收录这张图片」？**
那是扩展的图片菜单，只对 `<img>` 元素出现。如果是背景图或 canvas，
先把图存下来，再用上面的 curl 方式，或在网页端手动补录。

## 10. 自测与可以继续加的东西

改完代码想确认没弄坏东西（都不会动真实题库）：

```bat
python test_e2e.py      :: 接口冒烟测试，87 条用例，用临时库
python test_llm.py      :: AI 结构化，用本地假接口，不花 token
node test_collect.js    :: 扩展的「取文字 + 取图」逻辑（假 DOM，需要 Node）
```

`test_collect.js` 是用来补「本机没有 Chrome、没法端到端跑扩展」这个缺口的：
它把 `extension/background.js` 里的 `collectFromPage()` 抠出来，喂一个假 DOM，
验证选图、过滤装饰图、6 张上限、转不了时退回绝对 URL 这些行为。

可以继续加的东西：

- 知识点自动合并（同义词聚类）
- 测验历史与成绩曲线（现在只在交卷时判分，不存整卷记录）
- 按知识点生成思维导图
- 从学习通页面直接注入"收藏"按钮（比右键更顺手，但更依赖页面结构）
- 打包成单文件 exe（PyInstaller），让别人连 Python 都不用装
- 多设备同步（现在是纯本地单人）
- OCR 结果人工校对队列（低置信度的行标出来重点看）

## 11. 打包发给别人

```bat
python pack.py            :: 生成 dist\quizbank-1.0.2.zip
python pack.py --list     :: 只看会打包哪些文件
```

包里的规则是刻意写死的：

- **不会带 `.env`** —— 里面有你的 API key，带出去等于把额度送人。
- **不会带 `data\`** —— 你的题库和截图只属于你。
- 也不会带测试脚本和调试残留（`test_*.py`、`_*.png`、`__pycache__`）。

`pack.py` 写完 zip 会自己重新打开检查一遍，混进上面这些东西就直接报错。

对方拿到 zip 后：解压 → 看 `INSTALL.txt` → 双击 `start.bat`。他需要自备两样东西：

| 需要 | 说明 |
| --- | --- |
| Python 3.9+ | 安装时勾「Add Python to PATH」 |
| 自己的 `LLM_API_KEY` | 首次运行 `start.bat` 会自动从 `.env.example` 生成 `.env`，填进去即可 |

两点提醒：

- 浏览器扩展不能打包成 `.crx` 直接装，对方要在 `chrome://extensions/` 里用
  「加载已解压的扩展程序」选 `extension` 目录。
- 端口如果改了，`extension/manifest.json` 里那三处 `8765` 必须一起改，
  否则扩展的请求会被浏览器的权限模型拦下来。

## 12. 更新日志

### 1.0.2

- **安全加固：默认不再允许任意网页调用本机 API。** 以前 CORS 是 `*`，配合默认
  不校验 token，等于把你本机 `127.0.0.1:8765` 的读写权交给任何网页。现在默认只放行
  本机页面和浏览器扩展，需要放开时用 `QUIZBANK_CORS`。
- **安全加固：服务端下载远程图片（`image_url`）只允许公网地址。** 以前任何
  http(s) 地址都会去抓，包括 `127.0.0.1` 上的其它端口、局域网设备、云厂商的
  `169.254.169.254`，抓到的内容还能通过 `/images/` 读回来，是个 SSRF 通道。
  现在会解析域名、拒绝内网/环回/链路本地/保留地址，重定向逐跳校验（最多 3 跳）。
- **安全加固：下载内容必须真的是图片。** 按文件头判断 PNG/JPEG/GIF/BMP/WEBP/TIFF，
  伪装成图片的 HTML 直接拒绝，不会再往 `data/images/` 写非图片文件。
- `/api/health` 不再返回数据库绝对路径（原来会把 Windows 用户名带出来）。
- token 校验改用恒定时间比较；`data/images/` 增加 500 MB 总量上限。
- 新增 `LICENSE`（MIT）。
- 测试：`test_e2e.py` 106 通过（新增 [17] 安全加固 19 条）、`test_llm.py` 14 通过、
  `test_collect.js` 6 通过。测试里显式禁用真实 LLM，不会消耗你的 API 额度。

### 1.0.1

- **修复：题干里带图片时，只能收文字或者只能收图片，二选一。**
  现在选中文字右键一次，就能把「选中的文字 + 题干附近最多 6 张图」一起收录，
  存成同一道题。取图优先在页面内转 dataURL（带 cookie，需要登录的图床也拿得到），
  转不成的把绝对地址交给服务端下载；装饰性图片（图标、头像、占位图、1×1）会被过滤。
  同一道题重复收录时新图追加，不覆盖。
- 数据库新增 `image_paths` 列（存多图，自动迁移，老数据不受影响），
  `image_path` 保留为第一张图，老接口和导出不受影响。
- `/api/capture` 新增 `images` / `ocr` / `keep_image` 参数；
  返回里的 `images` 是实际存下的张数，失败的单张进 `image_errors`，不影响文字入库。
- 题库卡片、复习页、测验答题页、测验结果页都支持多图并排显示。
- 新增 `test_collect.js`：用假 DOM 测扩展的取图逻辑（本机没有 Chrome 也能跑）。
- 测试：`test_e2e.py` 87 通过、`test_llm.py` 14 通过、`test_collect.js` 6 通过。

### 1.0.0

- 首个版本：本地错题本 + 学科知识点 + SM-2 复习 + 模拟测验 + 截图 OCR + 导出 Anki/Markdown。
