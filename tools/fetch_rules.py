#!/usr/bin/env python3
"""fetch_rules.py —— 下载配置中引用的全部 RULE-SET 到本地 rules/sync/

用于离线比对域名、排查分流冲突。目录按来源分：
    rules/sync/blackmatrix7/<Name>.list
    rules/sync/AdRules/adrules.list
    rules/sync/iab0x00/AI.txt

用法：
    python3 tools/fetch_rules.py            # 只下载缺失的文件
    python3 tools/fetch_rules.py --force    # 全部重新下载

rules/sync/ 已在 .gitignore 中忽略，不会进入仓库。
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG = BASE_DIR / "lazy_group_plus.conf"
SYNC_DIR = BASE_DIR / "rules" / "sync"

# 来源映射：URL 片段 -> (子目录, 文件名)
SOURCES: list[tuple[str, str, str]] = [
    ("raw.githubusercontent.com/blackmatrix7/ios_rule_script", "blackmatrix7", ""),
    ("raw.githubusercontent.com/Cats-Team/AdRules", "AdRules", "adrules.list"),
    ("raw.githubusercontent.com/iab0x00/ProxyRules", "iab0x00", "AI.txt"),
]


def list_urls(config: Path) -> list[str]:
    if not config.exists():
        sys.exit(f"错误：找不到配置文件：{config}")
    urls: list[str] = []
    for ln in config.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s.startswith("RULE-SET,"):
            continue
        url = s[len("RULE-SET,"):].rsplit(",", 1)[0]
        if url.startswith("http"):
            urls.append(url)
    return sorted(set(urls))


def target_for(url: str) -> Path | None:
    for fragment, subdir, fixed_name in SOURCES:
        if fragment in url:
            name = fixed_name or url.rstrip("/").rsplit("/", 1)[-1]
            # blackmatrix7 的 URL 形如 .../rule/<平台>/<分类>/<文件>，取文件名即可
            return SYNC_DIR / subdir / name
    return None


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        dest.write_bytes(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="下载配置引用的 RULE-SET 到 rules/sync/")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = parser.parse_args()

    urls = list_urls(CONFIG)
    if not urls:
        sys.exit("错误：配置中没有找到任何 RULE-SET 规则集")

    ok = skipped = failed = 0
    for url in urls:
        dest = target_for(url)
        if dest is None:
            print(f"跳过（未识别来源）：{url}")
            skipped += 1
            continue
        if dest.exists() and not args.force:
            skipped += 1
            continue
        try:
            download(url, dest)
            ok += 1
            print(f"下载 {dest.relative_to(BASE_DIR)}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"失败 {url}：{exc}")
    print(f"完成：下载 {ok}，跳过 {skipped}，失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
