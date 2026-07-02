"""sina-collect-15min: 抓取新浪 7x24 快讯，追加写入 JSONL。

hermes cron 调度 */15 * * * *。deliver=local（不推送飞书，只做数据采集）。
"""
import json
import subprocess
import sys
from datetime import datetime

from _lib import (
    detect_hermes_home,
    detect_skill_root,
    emit,
    emit_err,
    load_hermes_env,
)

load_hermes_env()

HERMES_HOME = detect_hermes_home()
PYTHON = HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = HERMES_HOME / "hermes-agent" / "venv" / "bin" / "python"

COLLECTOR = detect_skill_root("sina-7x24-news") / "scripts" / "collector.py"
SAVE_DIR = HERMES_HOME / "data" / "sina-7x24"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

# 抓取 + 写盘
result = subprocess.run(
    [str(PYTHON), str(COLLECTOR), "--size", "60", "--json", "--save-to", str(SAVE_DIR)],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
)

# 解析 JSON 拿摘要
written = skipped = errors = skipped_invalid = 0
sample_titles: list[str] = []
try:
    data = json.loads(result.stdout)
    saved = data.get("saved") or {}
    written = saved.get("written", 0)
    skipped = saved.get("skipped_dup", 0)
    skipped_invalid = saved.get("skipped_invalid", 0)
    errors = saved.get("errors", 0)
    for item in (data.get("news") or [])[:3]:
        c = item.get("content", "") or item.get("title", "")
        if c:
            sample_titles.append(c)
except Exception:
    pass

# 拼飞书消息
status = "OK" if result.returncode == 0 else "WARN"
lines = [
    f"[{status}] 新浪 7x24 @ {datetime.now():%H:%M}",
    f"总数 {written + skipped + skipped_invalid} | 新增 {written} | 重复 {skipped} | 无效 {skipped_invalid} | 错误 {errors}",
]
if sample_titles:
    lines.append("最新快讯：")
    lines.extend(f"  - {t}" for t in sample_titles)
emit("\n".join(lines) + "\n")

if result.returncode != 0:
    emit_err(result.stderr[-500:])

# 透传子进程 exit code：子进程失败时让 cron scheduler 能正确告警
sys.exit(result.returncode)
