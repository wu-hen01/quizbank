"""打一个可以直接发给别人的发行包。

用法：
    python pack.py            # 生成 dist/quizbank-<版本>.zip
    python pack.py --list     # 只列出会打包哪些文件

打包规则（重要，别改错）：
  * 不打包 .env      —— 里面是你的 API key，发出去就等于把额度送人
  * 不打包 data/     —— 里面是你的题库和截图
  * 不打包测试与调试残留（test_*.py、_*.png、__pycache__ 等）
"""
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
OUT_DIR = ROOT / "dist"

# 目录整体打包
INCLUDE_DIRS = ["app", "static", "extension"]
# 单个文件打包（.env.example 必须在，拼错会导致对方没有配置模板）
INCLUDE_FILES = [
    "run.py",
    "start.bat",
    "requirements.txt",
    ".env.example",
    "README.md",
    "resplit.py",
]

SKIP_DIRS = {"__pycache__", "data", "dist", ".git", ".idea", ".vscode", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".bak", ".orig", ".zip"}
SKIP_FILES = {"pack.py", "make_icon.py", "test_e2e.py", "test_llm.py",
              ".DS_Store", "Thumbs.db", "desktop.ini"}
# 只跳过点文件。注意别把 "_" 加进来 —— app/__init__.py 会连包声明一起被打掉，
# 解压后虽然靠命名空间包还能跑，但那是运气，不是设计。
SKIP_PREFIXES = (".",)

INSTALL_TXT = """QuizBank {version} - 安装与使用说明
==================================================

这是一个本地运行的错题本 + 复习 + 模拟测验工具。
你的题目只存在自己电脑的 data\\quizbank.db 里，不上传到任何地方。

一、准备（3 分钟）
--------------------------------------------------
1. 操作系统：Windows 10 / 11。
   （截图 OCR 用的是 Windows 自带识别引擎，Mac / Linux 上只有文字收录，
     其它功能不受影响。）
2. Python 3.9 或更高版本。安装时务必勾选 "Add Python to PATH"。
   下载：https://www.python.org/downloads/windows/
   检查：打开命令提示符输入  python -V  能看到版本号即可。

二、启动
--------------------------------------------------
1. 双击 start.bat。
   第一次会自动安装依赖（需要联网，装 fastapi / uvicorn / httpx）。
2. 窗口里出现  http://127.0.0.1:8765  就是启动好了。
3. 浏览器打开这个地址，就是题库界面。
   要关掉服务：在那个黑窗口按 Ctrl+C，或直接关窗口。

三、安装浏览器扩展（可选，但没它就不能右键收录）
--------------------------------------------------
1. Chrome 打开  chrome://extensions/  ，右上角打开「开发者模式」。
   （Edge 用 edge://extensions/ ，一样。）
2. 点「加载已解压的扩展程序」，选择本目录下的 extension 文件夹。
3. 点扩展图标 -> 选项，确认服务地址是 http://127.0.0.1:8765 （默认就对），
   点「测试连接」应该显示成功。
4. 用法：
   * 在网页上选中题目文字 -> 右键 ->「收录到 QuizBank」
   * 在题目图片上 -> 右键 ->「收录这张图片到 QuizBank（OCR）」

四、配置 AI（可选，但强烈建议）
--------------------------------------------------
没有 AI 也能用：规则解析照样抽出题干、选项、答案，只是不会自动分学科、
不会自动打知识点标签、也不会写解析。

1. start.bat 首次运行会自动把 .env.example 复制成 .env。
2. 打开 .env，填上你自己的 key，例如：
       LLM_API_KEY=sk-你的key
   DeepSeek 的 key 在 https://platform.deepseek.com 申请，
   默认接口 https://api.deepseek.com/v1 、模型 deepseek-chat，不用改。
   也支持任何 OpenAI 兼容接口（改 LLM_BASE_URL / LLM_MODEL 即可）。
3. 改完重启服务（关掉窗口再双击 start.bat）。

五、常见问题
--------------------------------------------------
* 双击 start.bat 窗口一闪就没了
    多半是没装 Python，或装的时候没勾「Add Python to PATH」。
    也可以在命令提示符里进入本目录执行  python run.py  看具体报错。

* 扩展提示「连不上本地服务」
    服务没启动。先双击 start.bat，看到地址后再收录。

* 端口 8765 被别的程序占用
    改 .env 里的 QUIZBANK_PORT，同时把 extension\\manifest.json 里的
    三次 8765 改成同一个新端口，然后在扩展管理页点「重新加载」。

* 截图识别不出中文
    Windows 设置 -> 时间和语言 -> 语言 -> 添加「中文(简体)」语言包。

* 杀毒软件报警
    这是几个 Python 脚本 + 一个 Chrome 扩展，没有打包成 exe，不是病毒。
    介意的话就别在本机运行。

六、数据与备份
--------------------------------------------------
全部数据在 data\\ 目录：
    quizbank.db    SQLite 数据库（题目、知识点、复习计划、作答记录）
    images\\       收录的题目截图
备份 = 拷走整个 data 目录；想清空 = 删掉它，重启后会自动重建。

七、说明
--------------------------------------------------
本工具用于「收录并复习自己做过的题」。
批量爬取平台题库、或在考试中用题库检索答案属于学术不端，也违反平台服务协议，
请不要那样使用；作者和分发者不对这类用法负责。
"""


def detect_version() -> str:
    manifest = ROOT / "extension" / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        version = str(data.get("version") or "").strip()
        if version:
            return version
    except (OSError, ValueError):
        pass
    return "1.0.0"


def collect() -> list:
    """返回 [(绝对路径, 包内相对路径)]。"""
    entries = []

    for name in INCLUDE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise SystemExit("缺少必要文件：%s" % path)
        entries.append((path, name))

    for name in INCLUDE_DIRS:
        base = ROOT / name
        if not base.is_dir():
            raise SystemExit("缺少必要目录：%s" % base)
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            parts = set(rel.parts)
            if parts & SKIP_DIRS:
                continue
            if path.name in SKIP_FILES:
                continue
            if path.name.startswith(SKIP_PREFIXES):
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            entries.append((path, rel.as_posix()))
    return entries


def main() -> int:
    version = detect_version()
    entries = collect()

    if "--list" in sys.argv:
        for _, rel in entries:
            print("  ", rel)
        print("共 %d 个文件" % len(entries))
        return 0

    OUT_DIR.mkdir(exist_ok=True)
    stem = "quizbank-%s" % version
    target = OUT_DIR / (stem + ".zip")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 先把说明放第一个，解压后一眼看到
        zf.writestr(stem + "/INSTALL.txt", INSTALL_TXT.format(version=version))
        for path, rel in entries:
            zf.write(path, stem + "/" + rel)

    # ---- 自检：绝不能带出去的东西 ----
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    rel_names = [n.split("/", 1)[1] for n in names if "/" in n]
    forbidden = [
        n for n in names
        if n.endswith("/.env")
        or "/data/" in n
        or "/__pycache__/" in n
        or n.endswith((".pyc", ".log"))
        or (pathlib.PurePosixPath(n).name.startswith("_")
            and pathlib.PurePosixPath(n).name != "__init__.py")
    ]
    if forbidden:
        print("!! 包里混进了不该有的文件：")
        for n in forbidden:
            print("   ", n)
        return 1

    # ---- 自检：该有的东西不能少 ----
    required = [
        "INSTALL.txt", "start.bat", "run.py", "requirements.txt",
        "app/__init__.py", "app/main.py", "static/index.html",
        "extension/manifest.json",
    ]
    missing = [r for r in required if r not in rel_names]
    if missing:
        print("!! 包里缺少必要文件：")
        for n in missing:
            print("   ", n)
        return 1

    size_kb = target.stat().st_size / 1024.0
    print("已生成：%s" % target)
    print("  版本 %s · %d 个文件 · %.1f KB" % (version, len(names), size_kb))
    print("  顶层目录：%s" % stem)
    print()
    print("发给别人时：只发这个 zip。对方解压后看 INSTALL.txt，双击 start.bat。")
    print("提醒：需要对方自备 Python 3.9+，以及自己的 LLM_API_KEY。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
