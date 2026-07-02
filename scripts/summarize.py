# -*- coding: utf-8 -*-
"""sina-7x24-hourly-digest: 读取 JSONL 过去 N 分钟新闻，调 LLM 总结，输出 Markdown。

用法:
    python summarize.py
    python summarize.py --window 60 --model deepseek-chat
    python summarize.py --data-dir C:\\path\\to\\jsonl-dir

输出到 stdout（hermes cron --deliver feishu 会抓走）。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 不要再 reconfigure stdout 编码：父进程 sina_summarize.py 已经显式
# capture_output=True, encoding="utf-8" 读取子进程 pipe，期望子进程用
# raw binary stream 写 utf-8 字节。这里再 reconfigure 反而会在 pipe 模式
# 下触发 buffer 异常关闭 → 父进程读到空 stdout。
# 当直接 python summarize.py 时，stdout 是 console（Windows cp936），
# 写 utf-8 字节给 cp936 console 会被自动 fall back 到 ?，中文/emoji
# 显示可能不正常但不会丢数据；不影响 cron 投递链路。
# try:
#     sys.stdout.reconfigure(encoding="utf-8", errors="replace")
#     sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# except Exception:
#     pass


def _load_env_from_hermes_home() -> None:
    """
    hermes 把 API key 存在 HERMES_HOME/.env。直接跑脚本时不会自动加载，
    手动 parse 一次 KEY=VALUE 注入 os.environ。

    重要：对于 DEEPSEEK_API_KEY / FEISHU_APP_SECRET 这类敏感 key，
    **强制覆盖** session env（PowerShell session 可能保留了旧的 test key），
    避免用过期凭据。
    """
    # 候选位置（按优先级）
    candidates = [
        Path(os.environ.get("HERMES_HOME", "")) / ".env",
        Path.home() / ".hermes" / ".env",
    ]
    sensitive_keys = {"DEEPSEEK_API_KEY", "FEISHU_APP_SECRET", "FEISHU_APP_ID"}
    for path in candidates:
        if not path or not str(path) or not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if not k:
                    continue
                # 敏感 key 强制覆盖；其它 key 保留 session
                if k in sensitive_keys or k not in os.environ:
                    os.environ[k] = v
        except OSError:
            continue
        break

# 默认配置：data_dir 自动探测（HERMES_HOME/data/sina-7x24/）
def _default_data_dir() -> Path:
    """探测 JSONL 数据目录：$HERMES_HOME/data/sina-7x24/，可被 --data-dir 覆盖。"""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env) / "data" / "sina-7x24"
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return local / "hermes" / "data" / "sina-7x24"
    return Path.home() / ".hermes" / "data" / "sina-7x24"

DEFAULT_DATA_DIR = _default_data_dir()  # 兼容老代码用 const 的场景
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_WINDOW_MIN = 60
DEFAULT_MAX_TOKENS = 4000

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "hourly_digest.md"
)


def _load_news_in_window(
    data_dir: Path, window_min: int, now: datetime, start_offset_min: int = 0,
) -> List[Dict[str, Any]]:
    """
    从 data_dir/YYYY-MM-DD.jsonl 读 [now - window_min - start_offset_min, now - start_offset_min) 范围的入库新闻。
    按 savedAt（毫秒时间戳）过滤；createTime 也兼容（"YYYY-MM-DD HH:MM:SS"）。

    跨天场景：自动拼接 today + yesterday 两个文件。
    start_offset_min=0 时即"过去 window_min 分钟"；>0 时跳过最近 N 分钟、读更早的窗口。
    """
    items: List[Dict[str, Any]] = []
    end_ms = int((now - timedelta(minutes=start_offset_min)).timestamp() * 1000)
    start_ms = int((now - timedelta(minutes=window_min + start_offset_min)).timestamp() * 1000)

    for offset in (0, -1):
        d = (now + timedelta(days=offset)).date()
        path = data_dir / f"{d.isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # 优先用 savedAt（毫秒时间戳）—— 我们入库的真实时间
                    sa = obj.get("savedAt")
                    if isinstance(sa, (int, float)):
                        if sa < start_ms or sa >= end_ms:
                            continue
                    else:
                        # fallback createTime
                        ct = obj.get("createTime")
                        if isinstance(ct, str):
                            try:
                                dt = datetime.strptime(ct, "%Y-%m-%d %H:%M:%S")
                                ts = int(dt.timestamp() * 1000)
                                if ts < start_ms or ts >= end_ms:
                                    continue
                            except ValueError:
                                pass
                    items.append(obj)
        except OSError:
            continue

    # 按 savedAt 倒序（最新在前）；fallback createTime
    def _key(o: Dict[str, Any]) -> float:
        sa = o.get("savedAt")
        if isinstance(sa, (int, float)):
            return float(sa)
        ct = o.get("createTime")
        if isinstance(ct, str):
            try:
                return datetime.strptime(ct, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                pass
        return 0.0

    items.sort(key=_key, reverse=True)
    return items


def _format_news_for_prompt(news: List[Dict[str, Any]]) -> str:
    """把 news 列表格式化成给 LLM 的数据块。"""
    lines: List[str] = []
    for n in news:
        ct = n.get("createTime", "?")
        tag = n.get("tag", "")
        title = (n.get("content") or n.get("title") or "").strip()
        # 去掉 JSON 转义换行，让 prompt 更可读
        title = title.replace("\n", " ").replace("\r", " ")
        lines.append(f"- [{ct}] [{tag}] {title}")
    return "\n".join(lines)


def _load_prompt_template() -> str:
    """读 prompts/hourly_digest.md 模板，缺失则用内置默认。"""
    if PROMPT_PATH.exists():
        try:
            return PROMPT_PATH.read_text(encoding="utf-8")
        except OSError:
            pass
    # 内置 fallback（与用户给的 prompt 等价）
    return (
        "# Role\n你是拥有 20 年经验的资深财经与科技新闻主编……\n"
        "（详见 prompts/hourly_digest.md）\n"
    )


def _call_deepseek(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout: int = 90,
) -> Optional[str]:
    """调 DeepSeek chat completions API，返回生成文本；失败返回 None。"""
    try:
        import requests  # 复用 sina collector 的依赖
    except ImportError:
        return None
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    sys.stderr.write(
        f"[DEBUG] deepseek call: model={model} url={url} "
        f"key_tail={api_key[-4:] if api_key else 'EMPTY'} "
        f"user_prompt_len={len(user_prompt)}\n"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        if resp.status_code != 200:
            sys.stderr.write(
                f"[ERR] deepseek HTTP {resp.status_code}: {resp.text[:300]}\n"
            )
            return None
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as e:
        sys.stderr.write(f"[ERR] deepseek exception: {e}\n")
        return None


def _window_label(window_min: int) -> str:
    """把分钟数转成人类可读标签：60 -> '1 小时'，240 -> '4 小时'，30 -> '30 分钟'。"""
    if window_min <= 0:
        return "0 分钟"
    if window_min < 60:
        return f"{window_min} 分钟"
    hours = window_min / 60
    if hours == int(hours):
        return f"{int(hours)} 小时"
    return f"{hours:.1f} 小时".rstrip("0").rstrip(".")


def _split_user_block(
    template: str, news_text: str, now: datetime, window_min: int, start_offset_min: int = 0,
) -> str:
    """
    模板里以 {{NEWS}} {{NOW}} {{WINDOW_MIN}} {{WINDOW_LABEL}} {{WINDOW_START}} {{WINDOW_END}} 作为占位符，做字符串替换。
    WINDOW_START/END 是实际数据窗口的起止（考虑 start_offset）。
    """
    window_end = now - timedelta(minutes=start_offset_min)
    window_start = window_end - timedelta(minutes=window_min)
    t = template
    t = t.replace("{{NEWS}}", news_text)
    t = t.replace("{{NOW}}", now.strftime("%Y-%m-%d %H:%M:%S"))
    t = t.replace("{{WINDOW_MIN}}", str(window_min))
    t = t.replace("{{WINDOW_LABEL}}", _window_label(window_min))
    t = t.replace("{{WINDOW_START}}", window_start.strftime("%H:%M"))
    t = t.replace("{{WINDOW_END}}", window_end.strftime("%H:%M"))
    return t


def hermes_main(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Hermes skill 入口，返回 dict 让 agent/framework 处理。
    这里主要做：读数据 → 调 LLM → 输出 Markdown。
    """
    # 直接 python 跑时不会加载 .env，hermes 调起时会传 env 所以这是 noop
    _load_env_from_hermes_home()

    data_dir = Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR
    window = int(args.window or DEFAULT_WINDOW_MIN)
    start_offset = int(getattr(args, "start_offset", 0) or 0)
    now = datetime.now()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return {
            "success": False,
            "error": "DEEPSEEK_API_KEY not set in environment",
            "markdown": "",
        }

    news = _load_news_in_window(data_dir, window, now, start_offset)
    if not news:
        return {
            "success": True,
            "count": 0,
            "window_min": window,
            "markdown": f"### 过去 {_window_label(window)}无新增新闻\n",
        }

    # 把模板里 Context 的"基准时间"作为变量传给 LLM
    template = _load_prompt_template()
    news_text = _format_news_for_prompt(news)
    user_prompt = _split_user_block(template, news_text, now, window, start_offset)

    # system prompt 只约束输出格式：Role 已经在用户消息的 prompt 模板里
    # （prompts/hourly_digest.md 第 1-2 行），不要在这里重复定义
    system_prompt = (
        "请严格按用户消息中定义的 Markdown 格式输出简报，"
        "不要输出额外解释、前言或结尾总结。"
    )

    t0 = time.time()
    md = _call_deepseek(
        api_key=api_key,
        base_url=args.base_url or DEFAULT_BASE_URL,
        model=args.model or DEFAULT_MODEL,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=int(args.max_tokens or DEFAULT_MAX_TOKENS),
    )
    elapsed = time.time() - t0

    if md is None:
        return {
            "success": False,
            "count": len(news),
            "window_min": window,
            "error": "DeepSeek API call failed",
            "markdown": "",
        }

    return {
        "success": True,
        "count": len(news),
        "window_min": window,
        "elapsed_s": round(elapsed, 1),
        "model": args.model or DEFAULT_MODEL,
        "markdown": md.strip(),
    }


def _main() -> int:
    p = argparse.ArgumentParser(description="sina-7x24-hourly-digest")
    p.add_argument("--data-dir", help="JSONL 所在目录")
    p.add_argument("--window", type=int, default=DEFAULT_WINDOW_MIN, help="过去 N 分钟")
    p.add_argument("--start-offset", type=int, default=0,
                   help="跳过最近 N 分钟再取 window 范围（用于 06:00 am 拿 22:00-02:00 这种『早于现在』的窗口）")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    args = p.parse_args()

    result = hermes_main(args)

    if args.json:
        # stdout 强制 utf-8（hermes cron runner 按 GBK 读，避开 emoji）
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(json.dumps(result, ensure_ascii=False))
    else:
        # Markdown 直接打印（hermes cron 投递到飞书）
        # 关键：print() 默认用 stdout encoding（Windows = gbk），emoji 字符
        # 会抛 UnicodeEncodeError 把整个 print 干掉、stdout 完全空。
        # 这里先把 gbk 编不了的字符全替换成 ?，再用 utf-8 写到 buffer，
        # 让父进程 sina_summarize.py 能正确读到 utf-8 字节。
        if not result["success"]:
            err = result.get("error", "unknown")
            text = f"[WARN] summarize failed: {err}"
        else:
            text = result.get("markdown") or "（无内容）"
        try:
            # gbk 安全化（emoji 替换成 ?）
            safe = text.encode("gbk", errors="replace").decode("gbk", errors="replace")
            sys.stdout.buffer.write(safe.encode("utf-8", errors="replace"))
            sys.stdout.buffer.write(b"\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"[ERR] print failed: {e}\n")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(_main())
