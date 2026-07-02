# -*- coding: utf-8 -*-
"""
新浪财经 7x24 实时快讯采集脚本
适用于 Hermes AI Agent 框架的 skill 调用

用法:
    python sina_7x24_news.py --page 1 --size 30
    python sina_7x24_news.py --size 50 --tag 0 --json
    python sina_7x24_news.py --json --pretty
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import requests
except ImportError:
    print("缺少依赖: requests，请先 pip install requests", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
API_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"
ZHIBO_ID = 152              # 新浪 7x24 财经直播频道
REFERER = "https://finance.sina.com.cn"
TIMEOUT = 10                # 单次请求超时（秒）
MAX_RETRIES = 3             # 失败重试次数
RETRY_DELAY = 1.0           # 重试退避基数（秒）


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _clean_html(raw: str) -> str:
    """
    清洗 rich_text 字段中的 HTML 标签 / 实体，转为纯文本。
    """
    if not raw:
        return ""
    # 1) HTML 实体反转义
    text = unescape(raw)
    # 2) 去掉所有 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 3) 合并多余空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    新浪接口返回的字符串常被包裹成  `var xxx = {...};`  形式，
    这里用正则把第一个完整的 JSON 对象抠出来。
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _parse_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    把原始 feed 项转成结构化新闻对象。
    """
    content = _clean_html(item.get("rich_text", ""))
    tag_list = item.get("tag") or []
    tag = tag_list[0].get("name") if tag_list and isinstance(tag_list[0], dict) else None

    return {
        "id": item.get("id"),
        "content": content,
        "title": (content[:100] + "...") if len(content) > 100 else content,
        "createTime": item.get("create_time") or "",
        "tag": tag,
        "fetchedAt": int(time.time() * 1000),
    }


# ---------------------------------------------------------------------------
# 核心采集逻辑
# ---------------------------------------------------------------------------
def fetch_news(page: int = 1, page_size: int = 30,
               tag_id: int = 0, retries: int = MAX_RETRIES) -> List[Dict[str, Any]]:
    """
    抓取新浪 7x24 财经快讯。

    Args:
        page:       页码，从 1 开始
        page_size:  每页条数
        tag_id:     标签过滤，0 表示全部
        retries:    失败重试次数

    Returns:
        结构化新闻列表；失败时返回空列表
    """
    params = {
        "page": page,
        "page_size": page_size,
        "zhibo_id": ZHIBO_ID,
        "tag_id": tag_id,
        "dire": "b",        # 倒序：最新在前
        "dpc": 1,
        "_": int(time.time() * 1000),
    }
    headers = {
        "Referer": REFERER,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(API_URL, params=params, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()

            data = _extract_json(resp.text)
            if not data:
                raise ValueError("未从响应中解析到 JSON")

            feed_list = (
                data.get("result", {})
                    .get("data", {})
                    .get("feed", {})
                    .get("list", [])
            ) or []

            return [_parse_item(item) for item in feed_list]

        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(RETRY_DELAY * attempt)
            continue

    # 重试全部失败
    print(f"[ERROR] 抓取失败: {last_err}", file=sys.stderr)
    return []


# ---------------------------------------------------------------------------
# 本地持久化
# ---------------------------------------------------------------------------
def _make_dedup_key(item: Dict[str, Any]) -> Optional[str]:
    """
    生成一条新闻的去重键。
    优先用 createTime（新浪端的发布时间字符串，精确到秒），
    缺失时回退到 id。
    """
    ct = item.get("createTime")
    if ct and isinstance(ct, str) and ct.strip():
        return f"t:{ct.strip()}"
    _id = item.get("id")
    if _id is not None:
        return f"i:{int(_id)}"
    return None  # 既没时间也没 id 的不写入（避免重复）


def _load_existing_keys(path: Path) -> Set[str]:
    """
    读取 JSONL 文件中已有的去重键集合，用于去重。
    容忍空文件 / 文件不存在 / 损坏行。
    """
    keys: Set[str] = set()
    if not path.exists():
        return keys
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # 兼容旧版文件（用 id 去重写入的）：用同样规则生成 key
                    k = _make_dedup_key(obj)
                    if k:
                        keys.add(k)
                except json.JSONDecodeError:
                    # 损坏行跳过，不影响整体
                    continue
    except OSError:
        pass
    return keys


def _sort_jsonl_by_time_desc(path: Path) -> Optional[str]:
    """
    把整个 JSONL 文件按 createTime 倒序（最新在前）重写。
    用 tmp 文件 + os.replace 实现原子替换，避免中途崩溃导致文件损坏。
    损坏行（无法 JSON 解析）会被丢弃，但记录在返回的 error string 里。
    返回 None 表示成功；返回 str 表示错误信息。
    """
    if not path.exists():
        return None
    try:
        items: List[Dict[str, Any]] = []
        bad_lines = 0
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    bad_lines += 1

        # 排序键：createTime 字符串降序，空值排到最后（最旧）；fallback savedAt
        def _sort_key(obj: Dict[str, Any]) -> str:
            ct = (obj.get("createTime") or "").strip()
            if ct:
                return ct
            sa = obj.get("savedAt") or 0
            # savedAt 是毫秒时间戳，转成"看起来像 createTime"的字符串就行
            return f"!{sa}"

        items.sort(key=_sort_key, reverse=True)

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for obj in items:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        os.replace(tmp_path, path)
        if bad_lines:
            return f"dropped {bad_lines} corrupted line(s)"
        return None
    except OSError as e:
        return f"sort failed: {e}"


def _save_news_to_jsonl(
    news_list: List[Dict[str, Any]],
    save_dir: Path,
) -> Dict[str, Any]:
    if not news_list:
        return {"file": "", "written": 0, "skipped_dup": 0, "skipped_invalid": 0, "errors": 0}

    target_dir = Path(save_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {
            "file": str(target_dir),
            "written": 0,
            "skipped_dup": 0,
            "skipped_invalid": 0,
            "errors": len(news_list),
            "fatal": f"无法创建目录: {e}",
        }
    target_path = target_dir / f"{date.today().isoformat()}.jsonl"

    existing_keys = _load_existing_keys(target_path)
    written = 0
    skipped = 0
    skipped_invalid = 0
    errors = 0
    saved_at = int(time.time() * 1000)

    # 用追加模式打开；多进程并发追加由 OS 保证单次 write < PIPE_BUF 原子性
    try:
        with target_path.open("a", encoding="utf-8") as f:
            for item in news_list:
                key = _make_dedup_key(item)
                if key is None:
                    skipped_invalid += 1
                    continue
                if key in existing_keys:
                    skipped += 1
                    continue
                # 给每条加 savedAt 字段，方便后续按时间窗口过滤
                item_with_meta = dict(item)
                item_with_meta["savedAt"] = saved_at
                try:
                    f.write(json.dumps(item_with_meta, ensure_ascii=False) + "\n")
                    existing_keys.add(key)
                    written += 1
                except (OSError, TypeError):
                    errors += 1
    except OSError as e:
        return {
            "file": str(target_path),
            "written": written,
            "skipped_dup": skipped,
            "skipped_invalid": skipped_invalid,
            "errors": errors + 1,
            "fatal": str(e),
        }

    # 追加完后重排：整个文件按 createTime 倒序（最新在前），用 tmp+rename 原子替换。
    # N=1373 时 sort 几乎免费；只在有写入时做，避免每次重复 IO。
    sort_err: Optional[str] = None
    if written > 0:
        sort_err = _sort_jsonl_by_time_desc(target_path)

    return {
        "file": str(target_path.resolve()),
        "written": written,
        "skipped_dup": skipped,
        "skipped_invalid": skipped_invalid,
        "errors": errors,
        "sort_error": sort_err,
    }


# ---------------------------------------------------------------------------
# Hermes 入口
# ---------------------------------------------------------------------------
def hermes_main(args: argparse.Namespace) -> Dict[str, Any]:
    """
    作为 Hermes skill 被调用时的统一入口。
    始终返回 dict，方便框架序列化。
    """
    news_list = fetch_news(
        page=args.page,
        page_size=args.size,
        tag_id=args.tag,
        retries=args.retries,
    )

    result: Dict[str, Any] = {
        "success": len(news_list) > 0,
        "count": len(news_list),
        "source": "sina_7x24",
        "page": args.page,
        "page_size": args.size,
        "timestamp": int(time.time() * 1000),
        "news": news_list,
    }
    if not news_list:
        result["error"] = "未获取到快讯数据"
        return result

    # 可选：追加写入本地 JSONL（按天分文件，按 id 去重）
    save_to = getattr(args, "save_to", None)
    if save_to:
        save_result = _save_news_to_jsonl(news_list, Path(save_to))
        result["saved"] = save_result
        if save_result.get("errors", 0) > 0 or "fatal" in save_result:
            result["save_error"] = save_result.get("fatal") or "部分行写入失败"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="新浪财经 7x24 实时快讯采集 (Hermes skill)",
    )
    parser.add_argument("--page", type=int, default=1, help="页码，默认 1")
    parser.add_argument("--size", type=int, default=30, help="每页条数，默认 30")
    parser.add_argument("--tag", type=int, default=0, help="标签 ID，0=全部")
    parser.add_argument("--retries", type=int, default=MAX_RETRIES, help="重试次数")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument("--pretty", action="store_true", help="JSON 格式化输出")
    parser.add_argument(
        "--save-to",
        metavar="DIR",
        default=None,
        help="追加写入本地目录（按天生成 YYYY-MM-DD.jsonl，按 id 自动去重）",
    )

    args = parser.parse_args()

    if args.json:
        result = hermes_main(args)
        indent = 2 if args.pretty else None
        ensure_ascii = not args.pretty
        print(json.dumps(result, ensure_ascii=ensure_ascii, indent=indent))
        if not result["success"]:
            return 1
        # 抓取成功但保存失败时也算非零退出，方便 cron 报警
        if result.get("save_error"):
            print(f"[WARN] 保存失败: {result['save_error']}", file=sys.stderr)
            return 2
        return 0
    else:
        # 人类可读输出
        news_list = fetch_news(args.page, args.size, args.tag, args.retries)
        if not news_list:
            print("未获取到快讯数据")
            return 1
        for i, n in enumerate(news_list, 1):
            tag_str = f"[{n['tag']}] " if n.get("tag") else ""
            print(f"{i:>3}. {n['createTime']}  {tag_str}{n['content']}")
        if args.save_to:
            sr = _save_news_to_jsonl(news_list, Path(args.save_to))
            print(
                f"\n[saved] +{sr['written']} 条写入 {sr['file']}"
                f"（跳过 {sr['skipped_dup']} 条重复，错误 {sr['errors']} 条）"
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
