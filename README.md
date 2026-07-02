# sina-7x24-news

> Sina Finance 7x24 real-time news → cron collector → hourly LLM digest → Feishu push.
> 新浪财经 7x24 实时快讯 → 定时抓取 → 整点 LLM 简报 → 飞书推送。

**Windows-only.** Tested on Windows 10/11 + Python 3.10-3.13 + Hermes ≥ 7.

---

## Features

- **Every 15 min** — collect 60 latest news from Sina Finance 7x24, dedup by `createTime`, append to daily JSONL
- **Every hour 7:00-22:00** — DeepSeek summarizes the past 1 hour → Markdown digest → Feishu
- **6:00 AM & 6:01 AM** — split digest of "昨夜今晨" (22:00-02:00 + 02:00-06:00)
- **3:00 AM daily** — cleanup: hot data 30 days, gzip archive (91% compression)
- **Idempotent install** — re-run `sina-install` any time, safe

## Quick Start (5 min)

### 0. Prerequisites

| Dependency | Check |
|------------|-------|
| Python 3.10+ | `python --version` |
| Hermes AI Agent (≥ 7) | `hermes --version` |
| DeepSeek API key | Get one at [platform.deepseek.com](https://platform.deepseek.com) |
| (Optional) Feishu bot | See [Feishu setup](#4-feishu-bot-optional) |

### 1. Configure API key

```powershell
# PowerShell
$envFile = "$env:LOCALAPPDATA\hermes\.env"
if (!(Test-Path $envFile)) { New-Item -ItemType File -Path $envFile -Force | Out-Null }
Add-Content $envFile "DEEPSEEK_API_KEY=sk-your-key-here"
hermes gateway restart
```

Or copy `.env.example` to `%LOCALAPPDATA%\hermes\.env` and fill in.

### 2. Install skill

```powershell
# From Hermes hub
hermes skills install sina-7x24-news

# Or from source (development mode)
git clone https://github.com/qq296565302/sina-7x24-news.git
# Then copy sina-7x24-news/ to ~/.hermes/skills/
```

### 3. Run install.py

```powershell
python "$HOME\.hermes\skills\sina-7x24-news\scripts\install.py"
```

This will:
1. Copy **6 files** to `~/.hermes/scripts/` (`_lib.py` + 5 cron wrappers)
2. Write global entry `sina-install.cmd` to your PATH
3. Register **5 cron jobs** (see [Cron Schedule](#cron-schedule))
4. Verify `DEEPSEEK_API_KEY` exists in `~/.hermes/.env`

After this, `sina-install` is available globally:

```powershell
sina-install              # idempotent upgrade
sina-install --dry-run    # preview
sina-install --force-cron # rebuild cron jobs
sina-install --uninstall  # remove everything
```

### 4. Feishu bot (optional)

If you want digests pushed to Feishu:

1. Create an enterprise app at [open.feishu.cn](https://open.feishu.cn)
2. Enable permissions: `im:message:send_as_bot`, `im:message.p2p_msg`, `im:message.group_at_msg`
3. Set up WebSocket long connection (recommended) or Webhook
4. Add credentials to `~/.hermes/.env`:
   ```
   FEISHU_APP_ID=cli_xxxxxxxx
   FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
   ```
5. Send any message to the bot to trigger pairing
6. Send `/sethome` to set delivery channel
7. `hermes gateway restart`

> Without Feishu config, jobs still run — digests just print to stdout instead of delivering.

### 5. Start gateway (if not running)

```powershell
hermes gateway status
hermes gateway start
```

For auto-start on boot, add to Task Scheduler: `hermes gateway service install`.

### 6. Verify

```powershell
hermes cron run sina-collect-15min       # manual collect
hermes cron run sina-summarize-hourly    # manual digest
hermes cron list                         # see all 5 jobs
```

You should receive a Feishu message in 3-5 seconds with a 4-section Markdown digest (宏观 / 市场 / 科技 / 公司) + 主编点评.

---

## Cron Schedule

| Job | Schedule | Deliver | Window |
|-----|----------|---------|--------|
| `sina-collect-15min` | `*/15 * * * *` | local | — |
| `sina-summarize-hourly` | `0 7-22 * * *` | feishu | 1h |
| `sina-summarize-0600-am` | `0 6 * * *` | feishu | 22:00-02:00 (昨夜今晨·上半夜) |
| `sina-summarize-0600-pm` | `1 6 * * *` | feishu | 02:00-06:00 (昨夜今晨·下半夜) |
| `sina-cleanup-daily` | `0 3 * * *` | local | hot 30d, archive gzip |

---

## Data Layout

```
%LOCALAPPDATA%\hermes\data\sina-7x24\
├── 2026-07-01.jsonl              # hot (last 30 days)
├── 2026-07-02.jsonl
├── 2026-05-15.jsonl.gz           # cold (>30 days, ~91% compressed)
└── 2026-05-16.jsonl.gz
```

Each line is JSON: `{id, content, createTime, tag, savedAt}` (UTF-8, sorted by `createTime` desc, dedup).

To read archived data:
```powershell
# Decompress one (in place)
gzip -d "$env:LOCALAPPDATA\hermes\data\sina-7x24\2026-05-15.jsonl.gz"

# Or read directly with Python (no decompress needed)
python -c "import gzip; print(gzip.open(r'C:\Users\<you>\AppData\Local\hermes\data\sina-7x24\2026-05-15.jsonl.gz', 'rt', encoding='utf-8').read())"
```

---

## Customization

### Change digest style

Edit `prompts/hourly_digest.md` (the system prompt for DeepSeek). Then re-run:

```powershell
sina-install    # sync wrapper (no-op for prompt)
hermes cron run sina-summarize-hourly    # test
```

### Change collect frequency

```powershell
hermes cron list                          # find job id
hermes cron remove <collect-job-id>
hermes cron create "*/30 * * * *" --script sina_collect.py --deliver local --name sina-collect-30min --no-agent
```

### Change retention / cleanup

```powershell
# Default: 30 days hot, gzip forever
python scripts/cron/sina_cleanup.py

# Preview
python scripts/cron/sina_cleanup.py --dry-run

# Custom
python scripts/cron/sina_cleanup.py --hot-days 14
python scripts/cron/sina_cleanup.py --cold-days 365 --delete
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Feishu receives nothing | Send `/sethome` to the bot; check `~/.hermes/logs/agent.log` |
| `Feishu credentials missing` | Add `FEISHU_APP_ID` / `FEISHU_APP_SECRET` to `~/.hermes/.env` |
| Digest Chinese appears as `?` | Re-run `sina-install`; verify Hermes ≥ 7 with `subprocess.run(..., encoding="utf-8")` fix |
| Digest empty | LLM call failed; check DeepSeek API quota |
| Sina returns 429 | Add ≥ 30s between collects; default `*/15` already safe |
| Cron not firing | `hermes gateway status` — gateway must be running |
| Cron shows `disabled` | `hermes cron enable <name>` |

---

## Directory Structure

```
sina-7x24-news/
├── SKILL.md                        # for Hermes agent (auto-discover)
├── README.md                       # this file
├── CHANGELOG.md
├── LICENSE
├── .env.example
├── .gitignore
├── prompts/
│   └── hourly_digest.md            # DeepSeek system prompt
└── scripts/
    ├── collector.py                # public: collect + dedup + JSONL
    ├── summarize.py                # public: LLM digest
    ├── install.py                  # one-shot install/uninstall
    └── cron/
        ├── _lib.py                 # shared path/encoding/emoji utilities
        ├── sina_collect.py         # cron wrapper: 15min
        ├── sina_summarize.py       # cron wrapper: hourly
        ├── sina_summarize_am.py    # cron wrapper: 06:00 am window
        ├── sina_summarize_pm.py    # cron wrapper: 06:01 pm window
        └── sina_cleanup.py         # cron wrapper: 03:00 cleanup
```

---

## License

MIT — see [LICENSE](LICENSE).
