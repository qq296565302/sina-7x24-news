"""sina-summarize-hourly: LLM 总结过去 N 分钟新闻，输出飞书。

调度：
- sina-summarize-hourly：0 7-22 * * *（整点 1h 窗口，"整点简报"）
- sina-summarize-0600-am：0 6 * * *（4h 上半夜 22:00-02:00，"昨夜今晨·上半夜"）
- sina-summarize-0600-pm：1 6 * * *（4h 下半夜 02:00-06:00，"昨夜今晨·下半夜"）

am/pm 调度由 sina_summarize_am/pm.py 薄 wrapper 注入 --half 1/2 实现。
"""
import argparse
import subprocess
import sys
from datetime import datetime

from _lib import (
    detect_hermes_home,
    detect_skill_root,
    emit,
    load_hermes_env,
    strip_emoji,
)

load_hermes_env()

HERMES_HOME = detect_hermes_home()
PYTHON = HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = HERMES_HOME / "hermes-agent" / "venv" / "bin" / "python"

SUMMARIZER = detect_skill_root("sina-7x24-news") / "scripts" / "summarize.py"

# 窗口判断：6 点跑"夜间窗口"（可拆 4h+4h），其它整点跑"小时窗口"（1h）
ap = argparse.ArgumentParser(add_help=False)
ap.add_argument("--half", type=int, choices=[1, 2], default=0,
                help="6:00 整点专用：1=上半夜(22:00-02:00), 2=下半夜(02:00-06:00)")
args, _ = ap.parse_known_args()
now = datetime.now()
if now.hour == 6:
    if args.half == 1:
        window, label = 240, "昨夜今晨·上半夜"
    elif args.half == 2:
        window, label = 240, "昨夜今晨·下半夜"
    else:
        # 没传 --half 的 fallback：单条 8h（兼容老 caller）
        window, label = 480, "昨夜今晨"
else:
    window, label = 60, "整点简报"

# 跑 LLM 总结
result = subprocess.run(
    [str(PYTHON), str(SUMMARIZER), "--window", str(window)],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
)

# emoji→ASCII + 兜底清理（飞书 GBK 友好）
out = strip_emoji(result.stdout)
out = f"[{label}] 窗口={window}min  生成={now:%H:%M}\n" + out
emit(out)

sys.exit(result.returncode)
