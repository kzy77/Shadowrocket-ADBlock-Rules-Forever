#!/usr/bin/env python3
"""merge_plus.py —— 合成 lazy_group_plus.conf

从上游模板 lazy_group.conf 生成 lazy_group_plus.conf：
1. 把 custom_rules.conf 的自定义规则注入到 [Rule] 区最顶部
   （覆盖语义：Shadowrocket 自上而下匹配，先命中先生效）；
2. 把 custom_extra.conf 的 [Host] / [URL Rewrite] / [MITM] 段落合并进对应段落
   （[MITM] 的 hostname 与模板合并去重）。

上游 lazy_group.conf 更新后（git pull），重跑本脚本即可完成合并：
    python3 merge_plus.py

幂等：可重复运行，结果一致。锚点缺失或片段为空时报错退出，不写出残缺文件。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE = BASE_DIR / "lazy_group.conf"
FRAGMENT = BASE_DIR / "custom_rules.conf"
EXTRA = BASE_DIR / "custom_extra.conf"
OUTPUT = BASE_DIR / "lazy_group_plus.conf"

# 锚点：[Rule] 节头行，自定义块插入其后（规则列表最顶部，覆盖上游同域名规则）
ANCHOR = "[Rule]"
# 注入块标记（用于幂等移除旧块）
MARK_BEGIN_PREFIX = "# >>> custom-rules"
MARK_END = "# <<< custom-rules"

# 支持合并的自定义段落
EXTRA_SECTIONS = ("Proxy Group", "Host", "URL Rewrite", "MITM")

# ---- 规则集地址加速 ----
# 把 raw.githubusercontent.com 改写为 jsDelivr CDN，规避国内直连抽风。
# 关闭方式：python3 merge_plus.py --no-cdn
USE_CDN = True
RAW_RE = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$")
CDN_TPL = "https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}"

GENERATED_NOTE = (
    "# 本文件由 merge_plus.py 自动生成，请勿手工编辑！\n"
    "# 自定义规则请修改 custom_rules.conf（[Rule]）或 custom_extra.conf（[Host]/[URL Rewrite]/[MITM]），"
    "然后运行：python3 merge_plus.py\n"
)


def load_fragment() -> list[str]:
    if not FRAGMENT.exists():
        sys.exit(f"错误：片段文件不存在：{FRAGMENT}")
    lines = FRAGMENT.read_text(encoding="utf-8").splitlines()
    # 去掉片段中残留的旧标记（防止有人把标记误写进片段）
    lines = [
        ln for ln in lines
        if not ln.startswith(MARK_BEGIN_PREFIX) and ln.strip() != MARK_END
    ]
    if not any(ln.strip() and not ln.lstrip().startswith("#") for ln in lines):
        sys.exit(f"错误：片段文件没有任何有效规则：{FRAGMENT}")
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def parse_extra_sections(path: Path) -> tuple[dict[str, list[str]], list[str]]:
    """解析 custom_extra.conf，返回 ({段落名: 行列表}, 段落头之前的注释行)。

    段落内的注释行（含被注释掉的规则）与空行都保留；
    段落头之前的说明性注释作为 preamble 一并带入生成文件。
    """
    if not path.exists():
        return {}, []
    sections: dict[str, list[str]] = {}
    preamble: list[str] = []
    pending: list[str] = []  # 段落头之前的注释
    trailing: list[str] = []  # 当前段落尾部紧邻下一个段落头的注释
    current: str | None = None
    for ln in path.read_text(encoding="utf-8").splitlines():
        stripped = ln.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            # 上一段尾部的注释块归属本段落，作为其前导注释
            sections[current] = pending + trailing
            pending = []
            trailing = []
            continue
        if current is None:
            if not stripped:
                # 空行把前面的注释块与后续段落头隔开，归入 preamble
                preamble.extend(pending)
                pending = []
            elif stripped.startswith("#"):
                pending.append(ln.rstrip())
            continue
        if stripped.startswith("#"):
            trailing.append(ln.rstrip())
            continue
        if trailing:
            sections[current].extend(trailing)
            trailing = []
        sections[current].append(ln.rstrip())
    if trailing:
        sections[current].extend(trailing)
    preamble.extend(pending)
    # 去掉每段尾部空行
    for key in list(sections):
        while sections[key] and not sections[key][-1].strip():
            sections[key].pop()
    return {k: v for k, v in sections.items() if v}, preamble


def section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """返回段落 [start, end) 的行号区间（end 为下一个段落头或文件末尾）。"""
    start: int | None = None
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped == f"[{name}]" and start is None:
            start = i
            continue
        if start is not None and stripped.startswith("[") and stripped.endswith("]"):
            return start, i
    if start is None:
        return None
    return start, len(lines)


def merge_extra(lines: list[str], extra: dict[str, list[str]]) -> list[str]:
    """把自定义段落合并进模板行列表；[MITM] 的 hostname 做并集合并。"""
    result = lines[:]
    # 倒序处理，避免前面插入影响后面的行号
    for name in reversed(EXTRA_SECTIONS):
        if name not in extra:
            continue
        bounds = section_bounds(result, name)
        if bounds is None:
            sys.exit(f"错误：模板中不存在段落 [{name}]，无法合并 custom_extra.conf 对应内容")
        start, end = bounds
        payload = extra[name]

        if name == "MITM":
            # 收集模板与自定义的 hostname 条目，去重后写回一行
            entries: list[str] = []
            for ln in result[start + 1 : end]:
                if ln.strip().startswith("hostname"):
                    entries += [e.strip() for e in ln.split("=", 1)[1].split(",") if e.strip()]
            extra_hostnames: list[str] = []
            comment_lines: list[str] = []
            for ln in payload:
                if ln.strip().startswith("hostname"):
                    extra_hostnames += [e.strip() for e in ln.split("=", 1)[1].split(",") if e.strip()]
                elif ln.lstrip().startswith("#"):
                    comment_lines.append(ln)
            if not extra_hostnames and not comment_lines:
                continue
            merged = entries + [h for h in extra_hostnames if h not in entries]
            new_line = ("hostname = " + ", ".join(merged)) if merged else None
            # 定位原 hostname 行替换，找不到则追加
            replaced = False
            for i in range(start + 1, end):
                if result[i].strip().startswith("hostname") and new_line:
                    result[i] = new_line
                    replaced = True
                    break
            insert_lines = ([new_line] if new_line else []) + comment_lines
            if not replaced:
                insert_at = end
                while insert_at - 1 > start and not result[insert_at - 1].strip():
                    insert_at -= 1
                result[insert_at:insert_at] = insert_lines
            elif comment_lines:
                pos = result.index(new_line) + 1
                result[pos:pos] = comment_lines
            continue

        # [Host] / [URL Rewrite]：追加到段落末尾（跳过尾部空行）
        insert_at = end
        while insert_at - 1 > start and not result[insert_at - 1].strip():
            insert_at -= 1
        block = [f"# >>> custom-extra:{name} (from custom_extra.conf)"]
        block += payload
        block.append(f"# <<< custom-extra:{name}")
        result[insert_at:insert_at] = block
    return result


def is_ruleset_line(line: str) -> bool:
    """判断是否为 RULE-SET 规则行（含被注释掉的）。"""
    body = line.lstrip()
    if body.startswith("#"):
        body = body.lstrip("#").lstrip()
    return body.startswith("RULE-SET,")


def rewrite_cdn(line: str) -> str:
    """把 RULE-SET 行里的 raw.githubusercontent.com 地址改写为 jsDelivr。

    注释掉的规则（# RULE-SET,...）也一并改写，取消注释后即为加速地址。
    """
    if not USE_CDN:
        return line
    stripped = line.lstrip()
    prefix = line[: len(line) - len(stripped)]
    body = stripped
    hashed = False
    if body.startswith("#"):
        hashed = True
        body = body[1:].lstrip()
        prefix = prefix + "# " if prefix == "" else prefix + "# "
    if not body.startswith("RULE-SET,"):
        return line
    url, sep, policy = body[len("RULE-SET,"):].rpartition(",")
    if not sep:
        return line
    m = RAW_RE.match(url)
    if not m:
        return line
    new_url = CDN_TPL.format(owner=m.group(1), repo=m.group(2), branch=m.group(3), path=m.group(4))
    return f"{prefix}RULE-SET,{new_url},{policy}"


def main() -> int:
    global USE_CDN
    if "--no-cdn" in sys.argv[1:]:
        USE_CDN = False
    if not TEMPLATE.exists():
        sys.exit(f"错误：模板文件不存在：{TEMPLATE}")

    fragment = load_fragment()
    extra, extra_preamble = parse_extra_sections(EXTRA)
    template_lines = TEMPLATE.read_text(encoding="utf-8").splitlines()

    # 定位锚点行
    anchor_idx = None
    for i, ln in enumerate(template_lines):
        if ln.strip() == ANCHOR:
            anchor_idx = i
            break
    if anchor_idx is None:
        sys.exit(f"错误：在 {TEMPLATE.name} 中找不到锚点行 {ANCHOR!r}，模板结构可能已变化，请人工核对")

    block = [MARK_BEGIN_PREFIX + " (generated by merge_plus.py, edit custom_rules.conf instead)"]
    block += fragment
    block.append(MARK_END)

    head_note = [GENERATED_NOTE.rstrip("\n")]
    if extra_preamble:
        head_note += (
            [""]
            + ["# >>> custom-extra:preamble (from custom_extra.conf)"]
            + extra_preamble
            + ["# <<< custom-extra:preamble"]
        )

    out_lines = (
        template_lines[: anchor_idx + 1]
        + head_note
        + block
        + template_lines[anchor_idx + 1 :]
    )

    if extra:
        out_lines = merge_extra(out_lines, extra)

    # 规则集地址统一加速（raw.githubusercontent.com -> jsDelivr）
    cdn_hits = 0
    for i, ln in enumerate(out_lines):
        if is_ruleset_line(ln):
            new_line = rewrite_cdn(ln)
            if new_line != ln:
                out_lines[i] = new_line
                cdn_hits += 1

    OUTPUT.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    merged_sections = ", ".join(f"[{s}]" for s in EXTRA_SECTIONS if s in extra) or "无"
    cdn_info = f"规则集 CDN 改写 {cdn_hits} 条" if USE_CDN else "CDN 改写已关闭"
    print(
        f"已生成 {OUTPUT.name}：注入 {len(fragment)} 行自定义规则到 [Rule] 顶部（第 {anchor_idx + 2} 行起）；"
        f"合并段落：{merged_sections}；{cdn_info}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
