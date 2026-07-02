# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-02

### Added
- Sina Finance 7x24 news collector with createTime-based dedup, atomic write, daily JSONL
- Hourly LLM digest (DeepSeek), window 60min for 7-22, 4h+4h split for 6:00 "昨夜今晨"
- Auto Feishu delivery (no manual channel config — uses home channel)
- Daily cleanup job: hot data 30 days + gzip archive (91% compression ratio)
- 5 cron jobs: `sina-collect-15min`, `sina-summarize-hourly`, `sina-summarize-0600-am`, `sina-summarize-0600-pm`, `sina-cleanup-daily`
- `install.py`: one-shot install/upgrade/uninstall, supports `--dry-run`/`--force-cron`/`--uninstall`
- Global entry shim: `sina-install.cmd` (Windows) / `sina-install` (POSIX)
- Public functions: `collector.py` and `summarize.py` for agent invocation
- Empirically tuned prompt with strict 60-150 char/news, 20-25 news/1h, 25-30 news/4h, no emoji output
- "主编点评" constrained to 60-100 char, must cite specific data, no boilerplate
- Wrapper `_lib.py` with path detection, encoding detection, emoji normalization, env loading
- Tested on Windows 10/11 + Python 3.10-3.13

### Fixed
- Herms cron scheduler `subprocess.run` `UnicodeDecodeError` due to implicit encoding (parent-side fix)
- pythonw.exe NUL stdout binding (use `os.write(1, bytes)`)
- Emoji ASCII normalization to prevent GBK encode failures

### Notes
- **Windows only**. macOS/Linux: not tested, may need path/encoding tweaks.
- Feishu delivery is optional. Without Feishu config, digests print to stdout and don't deliver.
- Requires Hermes framework ≥ 7 with `subprocess.run(..., encoding="utf-8")` fix.
