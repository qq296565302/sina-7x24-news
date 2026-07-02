# sina-7x24-news — skills.sh Submission

## Repository

- **GitHub**: https://github.com/qq296565302/sina-7x24-news
- **Path**: `sina-7x24-news/`
- **License**: MIT
- **Version**: 1.0.0
- **Platform**: Windows only (10/11 + Python 3.10-3.13)

## Description

Sina Finance 7x24 real-time financial news collector + hourly LLM digest + Feishu push.

Cron-driven, hands-off news pipeline:
1. Every 15 min — fetch 60 latest news from Sina, dedup by createTime, append to daily JSONL
2. Every hour 7:00-22:00 — DeepSeek summarizes past 1h, sends Markdown digest to Feishu
3. 6:00 AM & 6:01 AM — split "昨夜今晨" digest (22:00-02:00 + 02:00-06:00)
4. 3:00 AM — cleanup: hot data 30 days, gzip archive (91% compression ratio)

## Tags

`news` `finance` `cron` `feishu` `chinese` `sina` `digest` `llm` `deepseek` `windows`

## Categories

`finance` `news-aggregator`

## Why publish on skills.sh

- **Unique vertical**: First Sina Finance 7x24 + LLM digest skill on skills.sh
- **Bilingual**: EN + 中文 quick start, no localization needed
- **Battle-tested**: 4 weeks of daily production use, clean separation of cron wrappers / public functions
- **Complete install story**: `install.py` does one-shot setup; no manual copy-paste
- **Open data**: stores all news in plain JSONL, no proprietary format

## Installation (from hub)

```powershell
hermes skills install sina-7x24-news
sina-install
```

## Installation (from source)

```powershell
git clone https://github.com/qq296565302/sina-7x24-news.git
Copy-Item -Recurse sina-7x24-news $env:USERPROFILE\.hermes\skills\
python "$HOME\.hermes\skills\sina-7x24-news\scripts\install.py"
```

## Quality Checklist

- [x] SKILL.md with YAML frontmatter (name / version / description / tags / license)
- [x] README.md (English, 5-min quick start, troubleshooting, customization)
- [x] LICENSE (MIT)
- [x] CHANGELOG.md (1.0.0 release notes)
- [x] .env.example (placeholders, no real secrets)
- [x] .gitignore (excludes __pycache__, .env, *.jsonl, *.bak)
- [x] No hardcoded credentials, channel IDs, or user paths
- [x] Idempotent install (`sina-install` safe to re-run)
- [x] Error handling in cron wrappers (exit code propagated)
- [x] Tested on Windows 10/11 + Python 3.10-3.13 + Hermes ≥ 7
- [x] 5 cron jobs, all registered via `install.py`

## Sample Output

```markdown
[整点简报] 窗口=60min  生成=12:00
过去 1 小时全球要闻 (11:00-12:00)
【宏观与政策】
[美联储动态] 沃什任命施瓦布为顾问，市场担忧其减少央行发声将加剧市场波动...
【市场与行情】
[美股收盘] 三大股指集体收跌，道指跌 0.02%，纳指跌 0.64%...
【科技与 AI 前沿】
[Meta 云业务] 筹划推出云基础设施业务，出售过剩 AI 算力...
【公司与资本】
[软银融资] 拟以 OpenAI 股权为担保，向高盛等银团筹集 100 亿美元贷款...
主编点评：Meta 进军云计算信号强化 AI 算力板块情绪...
```

## Links

- **Repository**: https://github.com/qq296565302/sina-7x24-news
- **Issue tracker**: https://github.com/qq296565302/sina-7x24-news/issues
- **Sina 7x24 source**: https://finance.sina.com.cn/7x24/
- **DeepSeek API**: https://platform.deepseek.com
- **Hermes framework**: https://github.com/<hermes-agent>
