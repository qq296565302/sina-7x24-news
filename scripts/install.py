# -*- coding: utf-8 -*-
"""install.py: 一键安装 sina-7x24-news 整套 skill（采集 + 整点 LLM 简报 + 6:00 昨夜今晨 + 凌晨清理）。

执行本脚本会自动完成：
  1. 复制 6 个 cron wrapper 到 ~/.hermes/scripts/（_lib.py + 5 个 wrapper）
  2. 写全局入口 sina-install.cmd / sina-install
  3. 注册 5 个 hermes cron job（sina-collect-15min、sina-summarize-hourly、
     sina-summarize-0600-am、sina-summarize-0600-pm、sina-cleanup-daily）
  4. 检查 .env 里 DEEPSEEK_API_KEY 是否存在
  5. 输出下一步操作指引

用法:
    python install.py                 # 一键安装（幂等：job 已存在会跳过）
    python install.py --dry-run       # 只打印要做什么
    python install.py --force-cron    # 强制重建 cron job（先 remove 再 create）
    python install.py --uninstall     # 反向：移除 wrapper + cron job
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 路径探测
# ---------------------------------------------------------------------------
SKILL_ROOT = Path(__file__).resolve().parents[1]  # sina-7x24-news/


def detect_hermes_home() -> Path:
    """探测 HERMES_HOME：env var > Windows 默认 > POSIX 默认 > 上溯推断。"""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        default = local / "hermes"
    else:
        default = Path.home() / ".hermes"
    if default.exists():
        return default
    # 上溯：本文件可能在 <hermes>/.hermes/skills/.../ 下
    for ancestor in SKILL_ROOT.parents:
        if (ancestor / "hermes-agent").is_dir():
            return ancestor
    return default


HERMES_HOME = detect_hermes_home()
SCRIPTS_DIR = HERMES_HOME / "scripts"
ENV_PATH = HERMES_HOME / ".env"

# wrapper 源 → 目标（保持文件名简洁：cron create --script 用纯文件名即可）
WRAPPER_PAIRS: List[Tuple[Path, Path]] = [
    (SKILL_ROOT / "scripts" / "cron" / "_lib.py",
     SCRIPTS_DIR / "_lib.py"),
    (SKILL_ROOT / "scripts" / "cron" / "sina_collect.py",
     SCRIPTS_DIR / "sina_collect.py"),
    (SKILL_ROOT / "scripts" / "cron" / "sina_summarize.py",
     SCRIPTS_DIR / "sina_summarize.py"),
    (SKILL_ROOT / "scripts" / "cron" / "sina_summarize_am.py",
     SCRIPTS_DIR / "sina_summarize_am.py"),
    (SKILL_ROOT / "scripts" / "cron" / "sina_summarize_pm.py",
     SCRIPTS_DIR / "sina_summarize_pm.py"),
    (SKILL_ROOT / "scripts" / "cron" / "sina_cleanup.py",
     SCRIPTS_DIR / "sina_cleanup.py"),
]

# cron job 规格
# 注意：6:00 简报拆成 2 个 job（am 4h + pm 4h），sina-summarize-hourly 只覆盖 7-22 整点
# 凌晨 3 点跑 cleanup，错开 6:00 的简报触发
CRON_JOBS = [
    {
        "name": "sina-collect-15min",
        "schedule": "*/15 * * * *",
        "script": "sina_collect.py",
        "deliver": "local",  # 只抓数据入库，不推送飞书
    },
    {
        "name": "sina-summarize-hourly",
        "schedule": "0 7-22 * * *",  # 整点 7:00-22:00；6:00 由 am/pm 负责
        "script": "sina_summarize.py",
        "deliver": "feishu",
    },
    {
        "name": "sina-summarize-0600-am",
        "schedule": "0 6 * * *",  # 06:00 昨夜今晨·上半夜 (22:00-02:00)
        "script": "sina_summarize_am.py",
        "deliver": "feishu",
    },
    {
        "name": "sina-summarize-0600-pm",
        "schedule": "1 6 * * *",  # 06:01 昨夜今晨·下半夜 (02:00-06:00)
        "script": "sina_summarize_pm.py",
        "deliver": "feishu",
    },
    {
        "name": "sina-cleanup-daily",
        "schedule": "0 3 * * *",  # 凌晨 3 点：热数据 30 天 + 冷数据 gzip 归档
        "script": "sina_cleanup.py",
        "deliver": "local",  # 不推送飞书，只本地处理
    },
]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def log(tag: str, msg: str) -> None:
    """统一打印格式，dry-run 时改前缀。"""
    print(f"[{tag}] {msg}")


def run(cmd: List[str], check: bool = True) -> Tuple[int, str, str]:
    """执行命令并返回 (returncode, stdout, stderr)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if check and p.returncode != 0:
            log("WARN", f"cmd 失败 ({p.returncode}): {' '.join(cmd)}\nSTDERR: {p.stderr[:300]}")
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        log("ERR", f"找不到命令: {cmd[0]} ({e})")
        return 127, "", str(e)


def cron_job_exists(name: str) -> bool:
    """检查 cron job 是否已注册。"""
    rc, out, _ = run(["hermes", "cron", "list"], check=False)
    if rc != 0:
        return False
    return name in out


def find_cron_job_id(name: str) -> Optional[str]:
    """从 hermes cron list 输出里抠出 job_id（8-12 位 hex）。"""
    import re
    rc, out, _ = run(["hermes", "cron", "list"], check=False)
    if rc != 0:
        return None
    for line in out.splitlines():
        if name in line:
            m = re.search(r"\b([0-9a-f]{8,12})\b", line)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def install_wrappers(dry_run: bool = False) -> int:
    """把 cron/ 下的 wrapper 复制到 ~/.hermes/scripts/。已存在则备份后覆盖。"""
    ok = 0
    for src, dst in WRAPPER_PAIRS:
        if not src.exists():
            log("ERR", f"源 wrapper 不存在: {src}")
            continue
        if dry_run:
            log("DRY", f"复制 {src.name} → {dst}")
            ok += 1
            continue
        try:
            SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                bak = dst.with_suffix(dst.suffix + ".bak")
                shutil.copy2(dst, bak)
                log("OK", f"备份旧版 → {bak.name}")
            shutil.copy2(src, dst)
            log("OK", f"安装 {dst.name} → {dst}")
            ok += 1
        except OSError as e:
            log("ERR", f"复制 {src} → {dst} 失败: {e}")
    return ok


# 全局 alias 入口模板（写到 ~/.hermes/scripts/，让用户在任何位置都能调起 install）
# 设计：alias 脚本本身**只做"找到 skill 路径然后转发"**，永远用最新版 install.py
SHIM_CMD_WIN = r"""@echo off
rem sina-install.cmd: 全局入口，调 ~/.hermes/skills/sina-7x24-news/scripts/install.py
rem 由 install.py 自动生成；用环境变量 / 默认路径探测 skill 根目录

setlocal
set "SINA_HOME="
if defined SINA_NEWS_HOME set "SINA_HOME=%SINA_NEWS_HOME%"
if not defined SINA_HOME if exist "%USERPROFILE%\.hermes\skills\sina-7x24-news" set "SINA_HOME=%USERPROFILE%\.hermes\skills\sina-7x24-news"
if not defined SINA_HOME if exist "%LOCALAPPDATA%\hermes\skills\sina-7x24-news" set "SINA_HOME=%LOCALAPPDATA%\hermes\skills\sina-7x24-news"
if not defined SINA_HOME if exist "%HERMES_HOME%\skills\sina-7x24-news" set "SINA_HOME=%HERMES_HOME%\skills\sina-7x24-news"
if not defined SINA_HOME (
    echo [ERROR] 找不到 sina-7x24-news skill，请先运行：hermes skills install sina-7x24-news 1>&2
    exit /b 1
)
python "%SINA_HOME%\scripts\install.py" %*
endlocal
"""

SHIM_SH = r"""#!/usr/bin/env bash
# sina-install: 全局入口，调 ~/.hermes/skills/sina-7x24-news/scripts/install.py
# 由 install.py 自动生成；永远用最新版的 install.py
set -e
SINA_HOME="${SINA_NEWS_HOME:-}"
if [ -z "$SINA_HOME" ] && [ -d "$HOME/.hermes/skills/sina-7x24-news" ]; then
    SINA_HOME="$HOME/.hermes/skills/sina-7x24-news"
fi
if [ -z "$SINA_HOME" ] && [ -n "$HERMES_HOME" ] && [ -d "$HERMES_HOME/skills/sina-7x24-news" ]; then
    SINA_HOME="$HERMES_HOME/skills/sina-7x24-news"
fi
if [ -z "$SINA_HOME" ]; then
    echo "[ERROR] 找不到 sina-7x24-news skill，请先运行：hermes skills install sina-7x24-news" >&2
    exit 1
fi
exec python "$SINA_HOME/scripts/install.py" "$@"
"""


def install_shim(dry_run: bool = False) -> int:
    """在 ~/.hermes/scripts/ 写一个全局 alias 入口（Windows .cmd + POSIX .sh）。

    设计：alias 脚本只做"找到 skill 路径 → 调 install.py"，永远用最新版。
    """
    cmd_target = SCRIPTS_DIR / "sina-install.cmd"
    sh_target = SCRIPTS_DIR / "sina-install"
    ok = 0
    try:
        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log("ERR", f"创建 {SCRIPTS_DIR} 失败: {e}")
        return 0
    if dry_run:
        log("DRY", f"将写入 {cmd_target}")
        log("DRY", f"将写入 {sh_target}")
        return 1
    try:
        cmd_target.write_text(SHIM_CMD_WIN, encoding="utf-8")
        log("OK", f"写入全局入口 → {cmd_target}")
        ok += 1
    except OSError as e:
        log("ERR", f"写 {cmd_target} 失败: {e}")
    try:
        sh_target.write_text(SHIM_SH, encoding="utf-8")
        # POSIX 可执行位（Windows 上无意义，忽略 AttributeError）
        try:
            sh_target.chmod(0o755)
        except (OSError, NotImplementedError):
            pass
        log("OK", f"写入全局入口 → {sh_target}")
        ok += 1
    except OSError as e:
        log("ERR", f"写 {sh_target} 失败: {e}")
    return ok


def install_cron_jobs(dry_run: bool = False, force: bool = False) -> int:
    """注册 cron job；已存在则跳过（--force 时删了重建）。"""
    ok = 0
    for job in CRON_JOBS:
        name = job["name"]
        exists = cron_job_exists(name)

        if exists and not force:
            log("SKIP", f"cron job '{name}' 已存在，跳过（用 --force-cron 重建）")
            ok += 1
            continue

        if exists and force:
            job_id = find_cron_job_id(name)
            if job_id and not dry_run:
                log("OK", f"删除旧 job {name} ({job_id})")
                run(["hermes", "cron", "remove", job_id], check=False)
            elif dry_run:
                log("DRY", f"将删除 job {name}")

        cmd = [
            "hermes", "cron", "create", job["schedule"],
            "--name", name,
            "--script", job["script"],
            "--deliver", job["deliver"],
            "--no-agent",
        ]
        if dry_run:
            log("DRY", "将执行: " + " ".join(cmd))
            ok += 1
        else:
            rc, _, err = run(cmd, check=False)
            if rc == 0:
                log("OK", f"注册 cron job '{name}' (schedule={job['schedule']})")
                ok += 1
            else:
                log("ERR", f"创建 {name} 失败: {err[:200]}")
    return ok


def _emit_setup_required(keys: list) -> bool:
    """
    当缺关键配置时，输出一个 agent-readable 提示块（用 ===SETUP_REQUIRED===
    包起来）。Agent 读到这个块就知道该问用户什么问题、问完后写哪个 .env。

    返回 True 让调用方知道"已经进入 setup 等待态"——main() 会据此返回非零退出码，
    让 Agent 把控制权交回给用户，而不是继续往下跑 cron 注册。
    """
    import json
    payload = {
        "setup_required": True,
        "missing_keys": keys,
        "env_path": str(ENV_PATH),
        "apply_urls": {
            "DEEPSEEK_API_KEY": "https://platform.deepseek.com/api_keys",
        },
        "user_prompt_zh": (
            "为了让 sina-7x24-news 能生成 LLM 整点简报，需要配置：\n"
            + "\n".join(
                f"  • {k} → 申请地址：{_APPLY_URLS.get(k, 'N/A')}"
                for k in keys
            )
            + "\n\n请把对应的 key 发给我，我会自动写入 "
            f"{ENV_PATH}，然后继续完成部署。"
        ),
        "next_step_after_user_reply": (
            "收到用户的 key 后，追加写入 " + str(ENV_PATH) + "（注意保密），"
            "然后重新跑 `python install.py` 验证。如果所有 key 都齐了，"
            "install.py 会接着完成 cron job 注册。"
        ),
    }
    sys.stdout.write("\n===SETUP_REQUIRED===\n")
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n===END===\n")
    sys.stdout.flush()
    return True


# 申请地址映射（_emit_setup_required 用）
_APPLY_URLS = {
    "DEEPSEEK_API_KEY": "https://platform.deepseek.com/api_keys",
}


def check_api_key(dry_run: bool = False, interactive: bool = True) -> bool:
    """
    检查 DEEPSEEK_API_KEY 是否在 .env 或当前环境里。

    interactive=True（默认）时，缺 key 会输出 SETUP_REQUIRED 块并返回 False；
    设为 False 时只打印人类可读 WARN（兼容老行为）。
    """
    if os.environ.get("DEEPSEEK_API_KEY"):
        log("OK", "DEEPSEEK_API_KEY 已在环境变量里")
        return True
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "DEEPSEEK_API_KEY" and v.strip() and not v.strip().startswith("#"):
                    log("OK", f"DEEPSEEK_API_KEY 已在 {ENV_PATH} 里")
                    return True
        except OSError:
            pass

    # 缺 key
    if interactive:
        _emit_setup_required(["DEEPSEEK_API_KEY"])
    else:
        log("WARN", f"未找到 DEEPSEEK_API_KEY，请在 {ENV_PATH} 添加：")
        print(f"        DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx")
    return False


def uninstall(dry_run: bool = False) -> int:
    """反向操作：移除 cron job + wrapper 文件。"""
    ok = 0
    for job in CRON_JOBS:
        name = job["name"]
        job_id = find_cron_job_id(name)
        if not job_id:
            log("SKIP", f"job '{name}' 不存在")
            continue
        if dry_run:
            log("DRY", f"将删除 job {name} ({job_id})")
        else:
            rc, _, _ = run(["hermes", "cron", "remove", job_id], check=False)
            if rc == 0:
                log("OK", f"删除 job {name} ({job_id})")
                ok += 1
    for _, dst in WRAPPER_PAIRS:
        if not dst.exists():
            continue
        if dry_run:
            log("DRY", f"将删除 {dst}")
        else:
            try:
                dst.unlink()
                log("OK", f"删除 {dst}")
                ok += 1
            except OSError as e:
                log("ERR", f"删除 {dst} 失败: {e}")
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description="sina-7x24 一键安装")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不实际执行")
    p.add_argument("--force-cron", action="store_true", help="强制重建 cron job")
    p.add_argument("--uninstall", action="store_true", help="反向：移除 wrapper + cron job")
    p.add_argument("--skip-api-key-check", action="store_true", help="跳过 DEEPSEEK_API_KEY 检查")
    args = p.parse_args()

    print("=" * 60)
    print(f"HERMES_HOME: {HERMES_HOME}")
    print(f"SCRIPTS_DIR: {SCRIPTS_DIR}")
    print(f"SKILL_ROOT:  {SKILL_ROOT}")
    print("=" * 60)

    if args.uninstall:
        n = uninstall(dry_run=args.dry_run)
        log("DONE", f"uninstall 完成 ({n} 项)")
        return 0

    print("\n[1/4] 安装 cron wrapper ...")
    n1 = install_wrappers(dry_run=args.dry_run)

    print("\n[2/4] 写入全局 alias 入口 ...")
    n_shim = install_shim(dry_run=args.dry_run)

    print("\n[3/4] 注册 cron job ...")
    n2 = install_cron_jobs(dry_run=args.dry_run, force=args.force_cron)

    print("\n[4/4] 检查依赖 ...")
    api_ok = True
    if not args.skip_api_key_check:
        api_ok = check_api_key(dry_run=args.dry_run, interactive=not args.dry_run)

    print("\n" + "=" * 60)
    if not api_ok and not args.dry_run:
        # 缺 key：注册 cron 也不可靠（summarize 会失败），且已输出 SETUP_REQUIRED 块
        # 让 Agent 接走；用户填好 key 再重跑时，所有步骤会自动完成
        log("WAIT", "DEEPSEEK_API_KEY 缺失，已暂停注册后续步骤。Agent 请把 SETUP_REQUIRED 块发回给用户。")
        print("=" * 60)
        return 2  # 特殊退出码：2 = setup_required，让 Agent 知道要回去问用户
    if args.dry_run:
        log("DRY-RUN", "没有改动实际执行。去掉 --dry-run 真正安装。")
    else:
        log("DONE", f"wrapper={n1}/{len(WRAPPER_PAIRS)}  cron={n2}/{len(CRON_JOBS)}  shim={n_shim}/2")
        if n2 == len(CRON_JOBS):
            print()
            print("接下来：")
            print("  1. 确认 .env 里有 DEEPSEEK_API_KEY（脚本会提示）")
            print("  2. 启动 gateway（如果还没启动）：hermes gateway start")
            print("  3. 查看 job 状态：hermes cron list")
            print("  4. 手动触发：hermes cron run sina-collect-15min")
            print()
            print("今后再跑 install：直接打 sina-install（已写到 ~/.hermes/scripts/）")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
