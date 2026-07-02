"""sina-7x24-news cron wrapper 公共层。

提供：
- 路径探测：detect_hermes_home / detect_skills_root / detect_skill_root
- env 加载：load_hermes_env
- 写出：emit / emit_err（处理 pythonw.exe NUL stdout + encoding 自适应）
- emoji 表：EMOJI_MAP（飞书 GBK 友好）

约定：本模块不 import skill 内的业务模块（collector / summarize），
保持纯净、可单测、可被多个 wrapper 复用。
"""
import locale
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径探测
# ---------------------------------------------------------------------------
def detect_hermes_home() -> Path:
    """探测 hermes 安装根目录。

    优先级：
    1. $HERMES_HOME 环境变量
    2. Windows %LOCALAPPDATA%/hermes
    3. POSIX ~/.hermes
    4. 上溯 __file__ 寻找含 hermes-agent/ 的祖先目录
    """
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        default = local / "hermes"
    else:
        default = Path.home() / ".hermes"
    if default.exists():
        return default
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "hermes-agent").is_dir():
            return ancestor
    return default


def detect_skills_root() -> Path:
    """探测 skill 安装根目录。

    约定：skill 装在 ~/.hermes/skills/（即 %USERPROFILE%/.hermes/skills/），
    NOT 在 $HERMES_HOME/skills/。两套目录在 Windows 上完全分离。
    """
    env = os.environ.get("HERMES_SKILLS_ROOT")
    if env:
        return Path(env)
    if os.name == "nt":
        home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    else:
        home = Path.home()
    default = home / ".hermes" / "skills"
    if default.exists():
        return default
    return detect_hermes_home() / "skills"


def detect_skill_root(skill_name: str) -> Path:
    """探测单个 skill 的根目录（HERMES_SKILLS_ROOT/<skill_name>）。"""
    env = os.environ.get(f"{skill_name.upper().replace('-', '_')}_HOME")
    if env:
        return Path(env)
    return detect_skills_root() / skill_name


# ---------------------------------------------------------------------------
# env 加载
# ---------------------------------------------------------------------------
def load_hermes_env() -> None:
    """从 $HERMES_HOME/.env 加载 key 到 os.environ。

    hermes 框架在某些 caller 下（手动 `hermes cron run`）不会自动注入 .env，
    业务脚本里 `os.environ["DEEPSEEK_API_KEY"]` 拿不到值。
    这里手动兜底：敏感 key 强制覆盖，非敏感 key 填空。
    """
    candidates = []
    env = os.environ.get("HERMES_HOME", "")
    if env:
        candidates.append(Path(env) / ".env")
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        candidates.append(local / "hermes" / ".env")
    candidates.append(Path.home() / ".hermes" / ".env")
    sensitive_keys = {"DEEPSEEK_API_KEY", "FEISHU_APP_SECRET", "FEISHU_APP_ID"}
    for path in candidates:
        if not path or not str(path):
            continue
        try:
            if not path.exists():
                continue
        except OSError:
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if not k:
                    continue
                if k in sensitive_keys or k not in os.environ:
                    os.environ[k] = v
        except OSError:
            continue
        break


# ---------------------------------------------------------------------------
# 写出（处理 pythonw.exe NUL stdout + encoding）
# ---------------------------------------------------------------------------
def _detect_caller_encoding() -> str:
    """探测 caller 期望的 stdout encoding。

    hermes framework 跑 wrapper 时 subprocess.run 的 encoding 取决于 caller：
    - `hermes gateway run`：UTF-8 Mode（PYTHONIOENCODING=utf-8）
    - `hermes cron run <id>`（手动）：cp936/GBK
    wrapper 探测自己的 utf8_mode / sys.stdout.encoding，按 caller 期望的编码输出。
    """
    if sys.flags.utf8_mode:
        return "utf-8"
    try:
        enc = sys.stdout.encoding
        if enc:
            return enc
    except Exception:
        pass
    if os.name == "nt":
        return "cp936"
    try:
        return locale.getpreferredencoding(False) or "utf-8"
    except Exception:
        return "utf-8"


def emit(text: str) -> None:
    """写 stdout。

    用 os.write(1, bytes) 直接写 fd 1，绕过 Python IO 层：
    1. pythonw.exe 下 sys.stdout 被绑 NUL，print() 写的数据丢失
    2. 不创建第二个 FileIO 对象，避免解释器 shutdown 时 os.close(1) 关闭管道

    失败时 fallback 写 stderr（不静默吞错），让 cron 投递飞书报错时能看到原因。
    """
    enc = _detect_caller_encoding()
    try:
        os.write(1, text.encode(enc, errors="replace"))
    except Exception as e:
        try:
            sys.stderr.write(f"[_lib.emit FAIL] {type(e).__name__}: {e}\n")
            sys.stderr.flush()
        except Exception:
            pass


def emit_err(text: str) -> None:
    """写 stderr（语义同 emit）。"""
    enc = _detect_caller_encoding()
    try:
        os.write(2, text.encode(enc, errors="replace"))
    except Exception as e:
        # 写 stderr 都失败了（极少见），最后 try 一次 sys.stderr，失败则放弃
        try:
            sys.stderr.write(f"[_lib.emit_err FAIL] {type(e).__name__}: {e}\n")
            sys.stderr.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# emoji → ASCII 标签（飞书 GBK 编码下 emoji 会变 ?，先替换掉）
# ---------------------------------------------------------------------------
EMOJI_MAP = {
    "📅": "[Calendar]", "💡": "[Note]", "⭐": "[Star]", "📊": "[Chart]",
    "🌐": "[Global]", "🔔": "[Alert]", "⚠️": "[WARN]", "✅": "[OK]",
    "❌": "[ERR]", "🟢": "[OK]", "🔴": "[ERR]", "🟡": "[WARN]",
    "📈": "[Up]", "📉": "[Down]", "🚀": "[Rocket]", "🔥": "[Hot]",
    "💰": "[Money]", "🏦": "[Bank]", "🌍": "[Globe]", "🔍": "[Search]",
    "📌": "[Pin]", "🎯": "[Target]", "⚡": "[Flash]", "🤖": "[Bot]",
    "🧠": "[AI]", "💻": "[Tech]", "📱": "[Mobile]", "🏢": "[Corp]",
    "💼": "[Biz]", "📰": "[News]", "🗞️": "[News]",
    "🇨🇳": "[CN]", "🇺🇸": "[US]", "🇯🇵": "[JP]", "🇰🇷": "[KR]", "🇪🇺": "[EU]",
    "🪙": "[Coin]", "⛽": "[Oil]", "🥇": "[Gold]",
}

# 兜底：剩余 unicode blocks（真正的 emoji 块 + 旗帜 + 变体选择符）
# 故意**不包含** \u2600-\u27BF（杂项符号/装饰符号/几何形状/箭头/音乐等），
# 那个范围里有 ★ ☆ ℃ ℉ ① ② ←→ ★ 等**合法文本符号**，误伤代价高。
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
    "\U0001F1E0-\U0001F1FF"  # 旗帜
    "\uFE0F"                 # 变体选择符（emoji 风格）
    "\u200D"                 # ZWJ（多 emoji 组合）
    "]"
)


def strip_emoji(text: str) -> str:
    """1) 精确替换 EMOJI_MAP；2) 兜底删除剩余特殊符号。"""
    for k, v in EMOJI_MAP.items():
        text = text.replace(k, v)
    return _EMOJI_PATTERN.sub("", text)
