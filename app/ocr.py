"""图片 OCR。

默认走 Windows 自带的 OCR 引擎（Windows.Media.Ocr），不需要装任何第三方包，
中文识别质量对"网页题目截图"这类印刷体足够用。

识别失败的边界都返回 ("", 原因)，交给上层决定要不要提示用户。
"""
import base64
import binascii
import hashlib
import os
import re
import subprocess
import tempfile
from typing import Optional, Tuple

from . import config

_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "windows_ocr.ps1")

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
_MAX_BYTES = int(getattr(config, "OCR_MAX_BYTES", 12 * 1024 * 1024))

_engine_cache: Optional[Tuple[bool, str]] = None

# 汉字区间：基本区 + 扩展 A + 兼容区
_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

_ERR_TEXT = {
    "no-file": "图片不存在",
    "read-failed": "读图失败",
    "no-language": "系统没有安装中文 OCR 语言包",
    "recognize-failed": "识别失败",
    "no-astask": "系统不支持 WinRT 异步调用",
    "unexpected": "OCR 脚本异常",
}


def clean_text(text: str) -> str:
    """整理 Windows OCR 的输出。

    Windows 的中文识别会把每个汉字当成独立的"词"，词与词之间插空格，
    例如 "正 确 答 案"，直接拿去解析会把题干切碎，所以这里把紧挨着的
    汉字之间的空格去掉，同时保留英文单词周围原有的空格。
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 汉字 <-> 汉字
    text = re.sub(r"(?<=[%s])[ \t]+(?=[%s])" % (_CJK, _CJK), "", text)
    # 汉字 <-> 中文标点
    text = re.sub(r"(?<=[%s])[ \t]+(?=[，。、；：！？）】》”’·])" % _CJK, "", text)
    text = re.sub(r"(?<=[，。、；：！？（【《“‘·])[ \t]+(?=[%s])" % _CJK, "", text)
    # "3 ． （ 单 选 题 ）" 这类：数字/字母与中文标点之间
    text = re.sub(r"(?<=[0-9）】》”’])[ \t]+(?=[（【《“‘])", "", text)
    text = re.sub(r"[ \t]+(?=[．·])", "", text)
    text = re.sub(r"(?<=[．·])[ \t]+(?=[0-9A-Za-z])", "", text)

    # 选项行：小写的 "c." 统一成 "C."，并保证点后有一个空格
    text = re.sub(
        r"(?m)^[ \t]*([A-Ha-h])[ \t]*[.．、][ \t]*",
        lambda m: m.group(1).upper() + ". ",
        text,
    )
    # 题号后的空行、行尾空格
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def _explain(raw: str) -> str:
    """把 PowerShell 脚本返回的 QBERR|<code>|<detail> 翻成中文。"""
    body = raw.strip()
    if not body.startswith("QBERR|"):
        return body
    parts = body.split("|", 2)
    code = parts[1] if len(parts) > 1 else "unexpected"
    detail = parts[2] if len(parts) > 2 else ""
    label = _ERR_TEXT.get(code, "OCR 失败")
    detail = detail.strip()
    if detail and code in ("read-failed", "recognize-failed", "unexpected"):
        return "%s: %s" % (label, detail[:200])
    return label


def _powershell() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return path


def _run(image_path: str, lang: str = "") -> Tuple[str, str]:
    """调用 PowerShell 脚本识别图片，返回 (文本, 错误)。"""
    exe = _powershell()
    if not os.path.isfile(exe):
        return "", "找不到 Windows PowerShell"

    out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="ocr_")
    os.close(out_fd)
    cmd = [
        exe,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        _SCRIPT,
        "-Image",
        image_path,
        "-Out",
        out_path,
    ]
    if lang:
        cmd += ["-Lang", lang]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not os.path.isfile(out_path):
            detail = (proc.stderr or b"").decode("utf-8", "replace")[:300]
            return "", detail or "OCR 进程没有输出"
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except subprocess.TimeoutExpired:
        return "", "OCR 超时"
    except Exception as exc:  # pragma: no cover - 环境异常
        return "", "OCR 调用失败: %s" % exc
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass

    if text.startswith("ERROR: "):
        return "", _explain(text[7:].strip())
    return clean_text(text), ""


def available() -> Tuple[bool, str]:
    """探测本机 OCR 是否可用，结果缓存。"""
    global _engine_cache
    if _engine_cache is not None:
        return _engine_cache

    if not config.OCR_ENABLED:
        _engine_cache = (False, "OCR 已在配置里关闭")
        return _engine_cache
    if os.name != "nt":
        _engine_cache = (False, "当前系统不是 Windows，暂不支持内置 OCR")
        return _engine_cache
    if not os.path.isfile(_SCRIPT):
        _engine_cache = (False, "缺少 windows_ocr.ps1")
        return _engine_cache

    exe = _powershell()
    if not os.path.isfile(exe):
        _engine_cache = (False, "找不到 Windows PowerShell")
        return _engine_cache

    _engine_cache = (True, "Windows OCR")
    return _engine_cache


def decode_image(payload: str) -> Tuple[bytes, str]:
    """把 dataURL 或裸 base64 解成 (字节, 扩展名)。"""
    raw = (payload or "").strip()
    ext = ".png"
    if raw.startswith("data:"):
        header, _, body = raw.partition(",")
        if ";base64" not in header:
            raise ValueError("只支持 base64 编码的 dataURL")
        mime = header[5:].split(";")[0].lower()
        ext = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/bmp": ".bmp",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/tiff": ".tif",
        }.get(mime, ".png")
        raw = body
    else:
        raw = raw.replace("\n", "").replace("\r", "").replace(" ", "")
    if not raw:
        raise ValueError("没有收到图片数据")
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        raise ValueError("图片不是合法的 base64")
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > _MAX_BYTES:
        raise ValueError("图片超过 %d MB" % (_MAX_BYTES // 1024 // 1024))
    return data, ext


def recognize_bytes(data: bytes, ext: str = ".png", lang: str = "") -> Tuple[str, str]:
    """识别内存里的图片，返回 (文本, 错误)。"""
    ok, reason = available()
    if not ok:
        return "", reason
    if ext.lower() not in _ALLOWED_EXT:
        ext = ".png"

    fd, path = tempfile.mkstemp(suffix=ext, prefix="quizbank_img_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return _run(path, lang or config.OCR_LANG)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def recognize_file(path: str, lang: str = "") -> Tuple[str, str]:
    ok, reason = available()
    if not ok:
        return "", reason
    return _run(path, lang or config.OCR_LANG)


def save_image(data: bytes, ext: str = ".png") -> Tuple[str, str]:
    """把图片存到 data/images/，返回 (相对文件名, 用于 questions.image_path)。"""
    os.makedirs(config.IMAGE_DIR, exist_ok=True)
    digest = hashlib.sha1(data).hexdigest()[:20]
    if ext.lower() not in _ALLOWED_EXT:
        ext = ".png"
    name = "%s%s" % (digest, ext.lower())
    full = os.path.join(config.IMAGE_DIR, name)
    if not os.path.isfile(full):
        with open(full, "wb") as f:
            f.write(data)
    return name, full
