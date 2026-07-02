"""sina-cleanup-daily: 清理/归档过期的 sina-7x24 JSONL 数据。

策略（方案 B：压缩归档）：
- 热数据（最近 N 天，默认 30）：保持原 JSONL，LLM summarize 直接读
- 冷数据（> N 天）：gzip 压缩成 .jsonl.gz，节省 ~80% 空间
- 老数据（> M 天，默认永不删）：可选删除；默认永久保留

调度：每天凌晨 3 点（错开 6:00 简报触发）。

命令行：
    python sina_cleanup.py                        # 默认 30 天热数据，永久保留
    python sina_cleanup.py --hot-days 14          # 热数据 14 天
    python sina_cleanup.py --cold-days 365 --delete  # 冷数据 1 年外的删掉
    python sina_cleanup.py --data-dir <DIR>       # 自定义数据目录
    python sina_cleanup.py --dry-run              # 只打印计划，不实际改
"""
import argparse
import gzip
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from _lib import detect_hermes_home, emit, emit_err


def _default_data_dir() -> Path:
    """同 summarize.py 的 _default_data_dir()，单独拷一份避免循环引用。"""
    env = detect_hermes_home()
    return env / "data" / "sina-7x24"


def _format_size(n: int) -> str:
    """字节数 → 人类可读。"""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


def _gzip_file(path: Path) -> tuple[bool, int, int]:
    """gzip 压缩 path → path.gz。返回 (success, original_size, gz_size)。

    压缩成功后会删除原文件。失败时原文件保留。
    """
    gz_path = path.with_suffix(path.suffix + ".gz")
    if gz_path.exists():
        return False, 0, 0
    original_size = path.stat().st_size
    try:
        with path.open("rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        gz_size = gz_path.stat().st_size
        path.unlink()
        return True, original_size, gz_size
    except OSError:
        if gz_path.exists():
            gz_path.unlink()
        return False, original_size, 0


def _delete_file(path: Path) -> int:
    """删除文件，返回原大小。失败返回 0。"""
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError:
        return 0


def cleanup(data_dir: Path, hot_days: int, cold_days: int | None,
            delete_archived: bool, dry_run: bool) -> int:
    """主清理逻辑。返回 0 成功，1 失败。"""
    if not data_dir.is_dir():
        emit_err(f"[ERR] data dir 不存在: {data_dir}\n")
        return 1

    today = date.today()
    hot_cutoff = today - timedelta(days=hot_days)        # >= 这个日期的保留
    cold_cutoff = today - timedelta(days=cold_days) if cold_days else None  # < 这个日期的删

    compressed_count = 0
    compressed_bytes_in = 0
    compressed_bytes_out = 0
    deleted_count = 0
    deleted_bytes = 0
    skipped_count = 0
    error_count = 0

    # 扫描所有 .jsonl 和 .jsonl.gz
    for path in sorted(data_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix == ".gz":
            # 已经是压缩的；判断是否到了冷截止日期
            if cold_cutoff and delete_archived:
                file_date = _parse_date_from_name(path.name)
                if file_date and file_date < cold_cutoff:
                    if not dry_run:
                        size = _delete_file(path)
                        if size:
                            deleted_count += 1
                            deleted_bytes += size
                    else:
                        deleted_count += 1
            continue
        if path.suffix != ".jsonl":
            continue

        # 解析文件名 → 日期
        file_date = _parse_date_from_name(path.name)
        if not file_date:
            skipped_count += 1
            continue

        if file_date >= hot_cutoff:
            # 热数据：不动
            continue

        # 冷数据：gzip 压缩
        if dry_run:
            size = path.stat().st_size
            compressed_count += 1
            compressed_bytes_in += size
            compressed_bytes_out += int(size * 0.18)  # 估算 18%
        else:
            ok, orig, gz = _gzip_file(path)
            if ok:
                compressed_count += 1
                compressed_bytes_in += orig
                compressed_bytes_out += gz
            else:
                error_count += 1

    # 输出报告
    status = "OK" if error_count == 0 else "WARN"
    lines = [
        f"[{status}] sina-cleanup @ {datetime.now():%Y-%m-%d %H:%M}",
        f"策略: 热数据 {hot_days} 天 | 冷数据 gzip 归档 | "
        f"删除阈值: {cold_days} 天" if cold_days else f"策略: 热数据 {hot_days} 天 | 冷数据 gzip 归档 | 永久保留",
        f"扫描目录: {data_dir}",
    ]
    if dry_run:
        lines.append("[DRY-RUN] 实际未改动文件")
    lines.extend([
        f"压缩: {compressed_count} 个文件（{_format_size(compressed_bytes_in)} → {_format_size(compressed_bytes_out)}，"
        f"节省 {_format_size(compressed_bytes_in - compressed_bytes_out)}）",
    ])
    if deleted_count:
        lines.append(f"删除: {deleted_count} 个文件（{_format_size(deleted_bytes)}）")
    if skipped_count:
        lines.append(f"跳过: {skipped_count} 个文件（无法解析日期）")
    if error_count:
        lines.append(f"错误: {error_count} 个文件压缩失败")

    emit("\n".join(lines) + "\n")
    return 0 if error_count == 0 else 1


def _parse_date_from_name(filename: str) -> date | None:
    """从 '2026-06-29.jsonl' / '2026-06-29.jsonl.gz' 抠日期。"""
    stem = filename
    for suffix in (".jsonl.gz", ".jsonl"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description="sina-7x24 数据清理/归档")
    p.add_argument("--data-dir", type=Path, default=_default_data_dir(),
                   help="JSONL 数据目录（默认 $HERMES_HOME/data/sina-7x24/）")
    p.add_argument("--hot-days", type=int, default=30,
                   help="热数据保留天数（不压缩）；默认 30")
    p.add_argument("--cold-days", type=int, default=None,
                   help="冷数据最老保留天数（超过则删）；默认永不删")
    p.add_argument("--delete", action="store_true",
                   help="配合 --cold-days，超过 cold_days 的压缩文件也删掉")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印计划，不实际改文件")
    args = p.parse_args()

    if args.hot_days < 0:
        emit_err("[ERR] --hot-days 必须 >= 0\n")
        return 1
    if args.cold_days is not None and args.cold_days < args.hot_days:
        emit_err(f"[ERR] --cold-days ({args.cold_days}) 必须 >= --hot-days ({args.hot_days})\n")
        return 1
    if args.delete and args.cold_days is None:
        emit_err("[ERR] --delete 必须配合 --cold-days 使用\n")
        return 1

    return cleanup(
        data_dir=args.data_dir,
        hot_days=args.hot_days,
        cold_days=args.cold_days,
        delete_archived=args.delete,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
