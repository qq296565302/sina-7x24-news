"""薄 wrapper：固定参数读取昨夜今晨·上半夜数据（22:00-02:00 4h 窗口）。

实际工作由 sina_summarize.py 完成。本文件只负责传递参数。
- --window 240：4h 窗口
- --start-offset 240：跳过最近 4h（02:00-06:00 那段），让实际数据落在 22:00-02:00
- --half 1：让 sina_summarize.py 打"昨夜今晨·上半夜"标签
"""
import sys
from pathlib import Path

# 探测主 wrapper 位置：与本文件同目录的 sina_summarize.py
_here = Path(__file__).resolve().parent
_main = _here / "sina_summarize.py"
if not _main.exists():
    sys.stderr.write(f"[ERR] sina_summarize.py not found at {_main}\n")
    sys.exit(1)

# 把固定参数插到 argv 最前面
sys.argv = [str(_main), "--half", "1", "--window", "240", "--start-offset", "240", *sys.argv[1:]]
exec(compile(_main.read_text(encoding="utf-8"), str(_main), "exec"), {"__file__": str(_main), "__name__": "__main__"})
