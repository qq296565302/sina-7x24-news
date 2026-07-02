"""薄 wrapper：固定 --half 2（昨夜今晨·下半夜：02:00-06:00 4h 窗口）。

实际工作由 sina_summarize.py 完成。本文件只负责传递参数。
"""
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_main = _here / "sina_summarize.py"
if not _main.exists():
    sys.stderr.write(f"[ERR] sina_summarize.py not found at {_main}\n")
    sys.exit(1)

# 把 --half 2 插到 argv 最前面
sys.argv = [str(_main), "--half", "2", *sys.argv[1:]]
exec(compile(_main.read_text(encoding="utf-8"), str(_main), "exec"), {"__file__": str(_main), "__name__": "__main__"})
