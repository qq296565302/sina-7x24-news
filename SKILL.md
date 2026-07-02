---
name: "sina-7x24-news"
version: "1.0.0"
description: "Fetches Sina Finance 7x24 real-time financial news feed, stores it locally with dedup, and (optionally) generates an hourly LLM digest of the past N minutes. / 抓取新浪财经 7x24 实时快讯、按 createTime 去重追加写入 JSONL，并可整点生成 LLM 简报。Invoke when user needs latest market news, headlines, breaking financial events, or wants an hourly news digest pushed to Feishu. / 用户询问最新市场新闻、财经快讯、A股动态、突发财经事件，或想定期接收整点新闻简报时调用。也用于 install 意图：用户说「装 sina 新闻简报」「部署整点新闻」「接入财经快讯到飞书」「让飞书每小时收新闻」「sina 新闻简报怎么配」时，自动执行「Agent 引导安装流程」小节定义的 5 步。Returns list of {id, title, content, createTime, tag}, or Markdown digest via the LLM."
author: "qq296565302"
license: "MIT"
homepage: "https://github.com/qq296565302/sina-7x24-news"
repository: "https://github.com/qq296565302/sina-7x24-news"
tags: ["news", "finance", "cron", "feishu", "chinese", "sina", "digest", "llm", "deepseek"]
categories: ["finance", "news-aggregator"]
platform: "windows"
trust: "community"
---

# Sina 7x24 Financial News Fetcher + Hourly Digest

本 skill 提供**两件事**：
1. **采集** 新浪财经 7x24 实时快讯（`scripts/collector.py`）
2. **整点 LLM 简报** —— 每小时读过去 N 分钟的入库新闻，调 DeepSeek 生成 Markdown 简报（`scripts/summarize.py`）
3. **数据归档** —— 每天凌晨清理过期 JSONL，gzip 压缩归档避免存储溢出（`scripts/cron/sina_cleanup.py`）

通过 `scripts/install.py` 一键安装五个 cron job（1 个采集 + 3 个简报 + 1 个清理）。

## Quick Start

```bash
# 第 1 步：装 skill（一次性）
hermes skills install sina-7x24-news

# 第 2 步：跑 install.py 注册 cron job（一次性）
# install.py 会自动写入全局入口 sina-install.cmd 到 ~/.hermes/scripts/
# Windows（PowerShell / cmd 都用 %USERPROFILE%）：
python "%USERPROFILE%\.hermes\skills\sina-7x24-news\scripts\install.py"
# POSIX：
python "$HOME/.hermes/skills/sina-7x24-news/scripts/install.py"

# 之后想重新配置 / 升级 wrapper / 重建 cron job，直接打：
sina-install
```

> 第一次跑 `sina-install` 后，命令会永久写到 `~/.hermes/scripts/`。Windows 用户如果想直接打 `sina-install`（不带路径），需要把 `~/.hermes/scripts` 加到 PATH：
>
> ```powershell
> # PowerShell（用户级，永久）
> [Environment]::SetEnvironmentVariable("Path", $env:Path + ";$env:LOCALAPPDATA\hermes\scripts", "User")
> # 重开 PowerShell 后生效
> ```
>
> 或者直接用绝对路径：`& "C:\Users\m1316\AppData\Local\hermes\scripts\sina-install.cmd"`

**前置要求**：
1. `~/.hermes/.env` 里有 `DEEPSEEK_API_KEY=sk-xxxxxxxx`（LLM 总结用）
2. （可选）Feishu bot 已配对并设置 home channel（cron `--deliver feishu` 才能推送）
3. `hermes gateway` 在跑（cron job 调度靠它）

**完整文档**（CLI 用法、参数、返回结构、失败行为、API）见下文。

## 适用场景

- 用户询问"最新财经快讯"、"市场新闻"、"今天有什么财经新闻"
- 需要监控 A 股市场动态、政策变化
- LLM 需要把最新快讯作为上下文回答用户
- 自动化的新闻流抓取任务
- 想要定期收到"昨夜今晨 / 整点"新闻简报到飞书

## 接口

```
GET https://zhibo.sina.com.cn/api/zhibo/feed
  ?page=1
  &page_size=30
  &zhibo_id=152
  &tag_id=0
  &dire=b
  &dpc=1
  &_=<timestamp>
```

**请求头**：必须带 `Referer: https://finance.sina.com.cn`，否则会被拦截。

## 调用方式

### 作为 Hermes tool

```json
{
  "name": "sina_7x24_news",
  "description": "获取新浪财经 7x24 实时快讯",
  "parameters": {
    "type": "object",
    "properties": {
      "page":    { "type": "integer", "default": 1,  "description": "页码，从 1 开始" },
      "size":    { "type": "integer", "default": 30, "description": "每页条数" },
      "tag_id":  { "type": "integer", "default": 0,  "description": "标签 ID，0=全部" },
      "retries": { "type": "integer", "default": 3,  "description": "失败重试次数" },
      "save_to": { "type": "string",                "description": "可选，追加写入本地目录（按天生成 YYYY-MM-DD.jsonl，按 createTime 自动去重）" }
    }
  }
}
```

### 命令行

```bash
# 人类可读输出
python scripts/collector.py --size 50

# JSON 输出（推荐给 Agent）
python scripts/collector.py --json --pretty

# 抓取 + 追加写入本地（按天分文件、按 createTime 去重）
python scripts/collector.py --size 50 --json --save-to ~/.hermes/data/sina-7x24
```

### 作为 Python 模块

```python
import sys
sys.path.insert(0, "<path>/sina-7x24-news/scripts")
from collector import hermes_main
import argparse

result = hermes_main(argparse.Namespace(
    page=1, size=30, tag=0, retries=3
))
# result 是 dict，可直接交给框架 / 写入数据库 / 重试判断
```

## 返回结构

```json
{
  "success": true,
  "count": 30,
  "source": "sina_7x24",
  "page": 1,
  "page_size": 30,
  "timestamp": 1716700800000,
  "news": [
    {
      "id": 4959455,
      "title": "【存储芯片概念反复活跃 时空科技涨停创历史新高】存储芯片概念反...",
      "content": "【存储芯片概念反复活跃 时空科技涨停创历史新高】存储芯片概念反复活跃，时空科技涨停，创历史新高，兆易创新、大港股份、北京君正、普冉股份跟涨。",
      "createTime": "2026-06-29 09:43:57",
      "tag": "公司",
      "fetchedAt": 1782697461686
    }
  ]
}
```

> 说明：`title` 字段在 `content` 超过 100 字符时为其截断版（带 `...`），否则与 `content` 完全一致；如需严格区分，可忽略 `title` 直接使用 `content`。

## 失败行为

抓取失败（网络异常 / JSON 解析失败 / 全部重试耗尽）时：
- `success: false`
- `count: 0`
- `news: []`
- 多出一个 `error` 字段，描述最后一次的错误原因
- 进程退出码：`--json` 模式返回 1，文本模式返回 1

调用方判错示例：

```python
result = hermes_main(args)
if not result["success"]:
    log.error("抓取失败: %s", result.get("error"))
    return
for item in result["news"]:
    process(item)
```

## 本地持久化（`--save-to`）

`--save-to <DIR>` 把抓到的快讯**追加写入** `DIR/YYYY-MM-DD.jsonl`（按天分文件），用于构建历史数据流。

特性：
- **JSONL 格式**：每行一条新闻，UTF-8 编码
- **按 createTime 去重**：新浪端的发布时间字符串（精确到秒）作为主键；createTime 缺失时回退到 `id`
- **追加模式**：同一天多次抓取会自然累积
- **附加字段**：写入时多一个 `savedAt`（写入时刻毫秒时间戳）
- **脏数据隔离**：`createTime` 和 `id` 都缺失的行会跳过并计入 `skipped_invalid`
- **失败隔离**：目录打不开 / 写失败时，`result["save_error"]` 有值，但 `success` 仍反映抓取状态
- **退出码**：`--json` 模式下，抓取成功=0，抓取失败=1，抓取成功但保存失败=2（方便 cron 区分报警）

> **去重键生成规则**（`_make_dedup_key`）：
> - 优先：`t:<createTime>`（如 `t:2026-06-29 12:10:14`）
> - 兜底：`i:<id>`（如 `i:4959700`）
> - 都缺失：丢弃该条（避免重复写入）

返回示例（`saved` 字段）：

```json
{
  "success": true,
  "count": 60,
  "saved": {
    "file": "C:\\Users\\me\\.hermes\\data\\sina-7x24\\2026-06-29.jsonl",
    "written": 58,
    "skipped_dup": 2,
    "skipped_invalid": 0
  }
}
```

> 第二次抓取同一文件时，`skipped_dup` 几乎都是 `count - written`（去重生效）。
> 多次抓取会让 `count` 逐步增加，但 `skipped_dup` 占大头；只在新浪**新发布**快讯时才会有 `written > 0`。

## 整点 LLM 简报（`scripts/summarize.py`）

把上面持久化的 JSONL 数据，按时间窗口（默认 60 分钟）调 DeepSeek 生成结构化简报，输出 Markdown 到 stdout（`hermes cron --deliver feishu` 投递到飞书）。

**功能**：
- 读取 `$HERMES_HOME/data/sina-7x24/YYYY-MM-DD.jsonl`，按 `savedAt` 过滤过去 N 分钟
- **跨天支持**：自动拼接 today + yesterday 两个文件
- **LLM 总结**：调用 DeepSeek chat completions，按 4 大类（宏观/市场/科技/公司）输出结构化简报 + 主编点评
- **失败容错**：JSONL 损坏行 / 网络超时 / API 错误 → 返回失败退出码 1，cron 投递飞书报错

**命令行**：

```bash
# 默认：过去 60 分钟，模型 deepseek-chat
python scripts/summarize.py

# 自定义窗口
python scripts/summarize.py --window 480    # 8 小时（用于"昨夜今晨"）
python scripts/summarize.py --window 30     # 30 分钟

# 自定义数据目录 / 模型
python scripts/summarize.py --data-dir D:/data --model deepseek-reasoner
```

**输出示例**：

```
[整点简报] 窗口=60min  生成=15:00
### 过去 1 小时全球要闻 (14:00-15:00)

**【宏观与政策】**
* **[日本外交]** 日本首相高市早苗将对印度进行为期三天的访问...
* **[印尼债券]** 6月26日全球基金净买入3.46亿美元印尼债券...

**【市场与行情】**
* **[美股期货]** 纳指期货跌 0.4%，标普 500 期货跌 0.2%...

**【科技与AI前沿】**
* **[GPT-5.6]** OpenAI 发布 GPT-5.6，多模态能力大幅提升...

**【公司与资本】**
* **[比亚迪]** 比亚迪 7 月销量同比增长 12%，海外销量创历史新高...

---
**主编点评**：纳指期货盘前走弱 0.4%，市场对今晚美股科技股财报季开局持谨慎态度；同时日本-印度高层互访释放亚太经贸合作升温信号...
```

> wrapper (`scripts/cron/sina_summarize.py`) 内置 emoji 防御层：prompt 模板已禁止 LLM 输出 emoji，wrapper 另有一层 `EMOJI_MAP` 把 LLM 偶尔夹带的 emoji 替换成 ASCII 标签，保证飞书 GBK 编码不乱码。

**提示词模板**：`prompts/hourly_digest.md`（可编辑自定义简报风格）。模板里的占位符（运行时由 summarize.py 替换）：

| 占位符 | 含义 | 示例 |
|--------|------|------|
| `{{NEWS}}` | JSONL 里的新闻列表（每条带 `[createTime] [tag] content`）| `- [2026-07-01 15:00:00] [公司] xxx` |
| `{{NOW}}` | 当前时间 | `2026-07-01 15:00:00` |
| `{{WINDOW_MIN}}` | 时间窗口分钟数 | `60` |
| `{{WINDOW_LABEL}}` | 人类可读窗口 | `1 小时` / `4 小时` |
| `{{WINDOW_START}}` | 窗口起点 `HH:MM` | `14:00` |
| `{{WINDOW_END}}` | 窗口终点 `HH:MM` | `15:00` |

**依赖**：
- `DEEPSEEK_API_KEY` 环境变量（hermes 把 .env 加载到 `$HERMES_HOME/.env`）
- DeepSeek 兼容 base_url（默认 `https://api.deepseek.com`）

## 数据归档（`scripts/cron/sina_cleanup.py`）

每 15 分钟抓一次数据，每天 ~1000 条 / ~1 MB。长期积累会撑爆磁盘，**`sina-cleanup-daily` 每天凌晨 3 点自动处理**：

| 数据类型 | 保留策略 | 典型大小 |
|---------|---------|---------|
| **热数据**（最近 30 天）| 保持原 JSONL，LLM summarize 直接读 | 30 MB |
| **冷数据**（30 天 - 永久）| gzip 压缩成 `.jsonl.gz` | ~3 MB/年 |
| **超期**（默认无）| 不删（除非配 `--delete --cold-days 365`）| - |

压缩比实测 ~91%（9.3 KB → 821 B），**5 年总占用约 180 MB**（30 MB 热 + 150 MB 压缩归档），可接受。

**命令行**：

```bash
# 默认：30 天热数据，永久保留 gzip 归档
python scripts/cron/sina_cleanup.py

# 预览（不实际改）
python scripts/cron/sina_cleanup.py --dry-run

# 自定义策略
python scripts/cron/sina_cleanup.py --hot-days 14         # 14 天热数据
python scripts/cron/sina_cleanup.py --cold-days 365 --delete  # 1 年外的删掉

# 自定义数据目录
python scripts/cron/sina_cleanup.py --data-dir D:/data/sina-7x24
```

**解压历史数据**：

```bash
# 解压某天
gunzip ~/.hermes/data/sina-7x24/2026-05-01.jsonl.gz

# 批量解压
for f in ~/.hermes/data/sina-7x24/*.jsonl.gz; do gunzip "$f"; done
```

## 一键安装（推荐）

`scripts/install.py` 把"复制 wrapper + 创建 cron job + 检查 .env"三步合并为一条命令。

```bash
# 安装（幂等：cron job 已存在会跳过）
python scripts/install.py

# 预览要做什么
python scripts/install.py --dry-run

# 强制重建 cron job（删除旧的再创建）
python scripts/install.py --force-cron

# 卸载（删除 wrapper + cron job）
python scripts/install.py --uninstall
```

install.py 会做：
1. 复制 6 个文件到 `~/.hermes/scripts/`（`_lib.py` + 5 个 cron wrapper：`sina_collect.py` / `sina_summarize.py` / `sina_summarize_am.py` / `sina_summarize_pm.py` / `sina_cleanup.py`）
2. 注册五个 cron job：
   - `sina-collect-15min`（`*/15 * * * *`，抓数据 + 写 JSONL；`deliver=local` 不推送飞书）
   - `sina-summarize-hourly`（`0 7-22 * * *`，整点 1h 窗口 LLM 简报 + 飞书投递）
   - `sina-summarize-0600-am`（`0 6 * * *`，昨夜今晨·上半夜 22:00-02:00 4h 窗口 + 飞书投递）
   - `sina-summarize-0600-pm`（`1 6 * * *`，昨夜今晨·下半夜 02:00-06:00 4h 窗口 + 飞书投递）
   - `sina-cleanup-daily`（`0 3 * * *`，清理过期 JSONL + gzip 归档；`deliver=local` 不推送飞书）
3. 检查 `~/.hermes/.env` 里有没有 `DEEPSEEK_API_KEY`，**没有就输出 `===SETUP_REQUIRED===` 块并返回退出码 2**（让 Agent 知道该把控制权交回给用户）

> **重要约束**：`hermes cron create --script` 只接受 `~/.hermes/scripts/` 下的纯文件名（不支持绝对路径），所以 wrapper 必须放在那里。install.py 替你做这步。

## Agent 引导安装流程

**给 Agent 看的**。当用户表达以下任一意图时，按本节自动执行，不要让用户手动跑命令：

- 「装 sina 新闻简报」「部署整点新闻」「接入财经快讯到飞书」
- 「让飞书每小时收新闻」「配 sina 新闻定时推送」
- 「sina 新闻简报怎么配」「sina-7x24-news 怎么用」

### 步骤

#### 1. 检测 skill 是否已安装

```bash
hermes skills list | grep sina-7x24-news
```

未安装则：

```bash
hermes skills install sina-7x24-news
```

#### 2. 跑 install.py 检查依赖

```bash
python "$HERMES_HOME/skills/sina-7x24-news/scripts/install.py" 2>&1
echo "exit_code=$?"
```

观察输出和退出码：

- **退出码 0** → 全部就绪，跳到步骤 4
- **退出码 2** → 输出里有 `===SETUP_REQUIRED===` 块（见下文），跳到步骤 3
- **退出码 1 或其它** → 其它错误，把 stderr 反馈给用户，让他手动排查

#### 3. 收集 DEEPSEEK_API_KEY

`===SETUP_REQUIRED===` 块示例（install.py 自动生成）：

```
===SETUP_REQUIRED===
{
  "setup_required": true,
  "missing_keys": ["DEEPSEEK_API_KEY"],
  "env_path": "C:\\Users\\me\\AppData\\Local\\hermes\\.env",
  "apply_urls": {"DEEPSEEK_API_KEY": "https://platform.deepseek.com/api_keys"},
  "user_prompt_zh": "为了让 sina-7x24-news 能生成 LLM 整点简报，需要配置：...",
  "next_step_after_user_reply": "..."
}
===END===
```

**Agent 行为**：

1. 把块里的 `user_prompt_zh` 原样发给用户（**不要修改措辞**，用户能直接照着申请）
2. 等待用户回复 key（普通字符串即可，Agent 自己解析）
3. 用户回复后，**追加写入 `env_path` 指定的 .env**（保留其它 key，注意保密，不要 echo 给用户）：
   ```python
   # 推荐：Agent 直接调 file_append 工具，或用 shell:
   echo "DEEPSEEK_API_KEY=<用户给的 key>" >> "<env_path>"
   ```
4. 重新跑 install.py（回到步骤 2）

#### 4. 验证部署

```bash
hermes cron list
```

确认能看到以下 5 个 `active` job：
- `sina-collect-15min`（`deliver=local`）
- `sina-summarize-hourly`
- `sina-summarize-0600-am`
- `sina-summarize-0600-pm`
- `sina-cleanup-daily`（`deliver=local`）

可选：手工触发一次，立即收一条简报验证链路：

```bash
hermes cron run sina-summarize-hourly
```

#### 5. （可选）配置飞书推送

如果用户说要把简报推飞书，再走这两步；纯本地使用可跳过。

```bash
# 配对：扫码 / 复制 token
hermes feishu pair
```

配对完成后，在飞书里对 bot 发 `/sethome` 设定默认接收频道。

### 失败处理

| 现象 | 原因 | Agent 行为 |
|------|------|------------|
| `===SETUP_REQUIRED===` 块 + 退出码 2 | 缺 key | 走步骤 3 |
| 退出码 1，stderr 含 "找不到 hermes" | 用户没装 hermes | 引导用户先装 hermes |
| `hermes cron list` 看不到 job | install 没跑成功 | 跑 `python install.py --force-cron` 重建 |
| 飞书收不到消息 | 1) home channel 没设  2) gateway 没跑 | 1) 引导 `/sethome`  2) `hermes gateway status` |
| 飞书收到但内容是 "summarize failed" | DEEPSEEK_API_KEY 无效 | 引导用户去 https://platform.deepseek.com 检查余额/重置 key |

### 一句话总结给用户

完成所有步骤后，给用户一句简短的"已就绪"提示：

> "sina-7x24-news 已部署。`sina-collect-15min` 每 15 分钟采集（不推送飞书），`sina-summarize-hourly` 每天 7-22 点整点生成 1h 简报并推飞书，`sina-summarize-0600-am` / `-pm` 每天 6:00 / 6:01 各生成一条 4h『昨夜今晨』简报（拆 22:00-02:00 / 02:00-06:00）并推飞书。"

## 实现要点

1. **HTML 清洗**：`rich_text` 字段带 HTML 标签，需提取纯文本
2. **JSON 提取**：接口响应可能包成 `var xxx = {...}` 形式，需用正则抠 JSON
3. **重试机制**：失败自动重试 3 次，指数退避
4. **Referer 伪装**：必须带 Referer 才能拿到数据
5. **GBK 友好**：本接口返回 UTF-8，无需解码处理（与 hq.sinajs.cn 不同）

## 依赖

```
requests>=2.28.0
```

## 注意事项

- 单次抓取建议 `size ≤ 100`，避免触发限流
- 抓取频率建议 ≥ 30 秒/次
- 数据仅供学习研究使用，请遵守新浪的服务条款
