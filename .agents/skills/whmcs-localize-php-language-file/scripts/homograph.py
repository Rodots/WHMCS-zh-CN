#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""homograph.py - catch machine-translation homograph errors in a zh_CN language file.

These are not translation-style problems and not Traditional-character residue.
They are *wrong sense* errors: an English word that has several Chinese senses
was translated with the wrong one.

Real examples found in this project (all passed `langtool validate` and
`residue.py` with a clean bill of health, because the file was structurally
perfect and every character was Simplified):

    minutes  -> 会议纪要   (should be 分钟)
    tables   -> 桌子       (should be 表格)
    bold     -> 大胆       (should be 粗体)
    running  -> 跑步       (should be 运行中)
    ticket   -> 门票/机票  (should be 工单)
    apply    -> 申请       (should be 应用)
    flags    -> 旗帜       (should be 标志)
    keyTag   -> 钥匙标签   (should be 密钥标签)
    addfunds -> 加值       (should be 充值)
    TXT      -> 防晒 (纯色素) (should be TXT (文本))

Structural checks cannot see any of these: the PHP is valid, the placeholders
are intact, and the characters are all Simplified. The only way to catch them
is a curated list of known bad renderings plus a check of the *whole value*,
not just individual characters.

Checks
------
  1. EXACT_BAD    value (trimmed) equals a known wrong rendering      -> hard
  2. SUBSTRING_BAD a known wrong phrase occurs inside a value          -> hard
  3. GLOSSARY     a key's value differs from the project's fixed term  -> hard
  4. suspicious pure-ASCII technical abbreviations rendered as prose   -> advisory

Usage
-----
  python homograph.py chinese.php [--report report.txt]
  python homograph.py chinese.php --extra badlist.json   # add project rules

badlist.json format (list of objects):
  {"type": "exact",     "bad": "会议纪要", "good": "分钟", "note": "minutes"}
  {"type": "substring", "bad": "门票",     "good": "工单", "note": "ticket"}
  {"type": "glossary",  "key": "apply",   "good": "应用", "note": "Apply button"}

Exit codes: 0 clean / 1 problems found / 2 usage or IO error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        if (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Matches `$_LANG['a']['b'] = "value";` and `$_ADMINLANG[...]` alike.
RX = re.compile(
    r"""^\s*(?P<var>\$[A-Za-z_][A-Za-z0-9_]*)\s*"""
    r"""(?P<keys>(?:\[\s*(?P<q>['"])(?P<key>[^'"]*)(?P=q)\s*\])*)\s*=\s*"""
    r"""(?P<qq>['"])(?P<value>.*)(?P=qq);\s*$"""
)
KEY_RX = re.compile(r"""\[\s*['"]([^'"]*)['"]\s*\]""")
CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

# type, bad, good, note
EXACT_BAD = [
    ("分钟", "会议纪要", "minutes (time unit) mistaken for meeting notes"),
    ("分钟", "会议记录", "minutes (time unit) mistaken for meeting notes"),
    ("表格", "桌子", "tables (Markdown) mistaken for furniture"),
    ("粗体", "大胆", "bold (style) mistaken for brave"),
    ("运行中", "跑步", "running (VM state) mistaken for jogging"),
    ("工单", "门票", "ticket (support) mistaken for an admission ticket"),
    ("工单", "机票", "ticket (support) mistaken for an airline ticket"),
    ("应用", "申请", "apply (button) mistaken for apply-for"),
    ("标志", "旗帜", "flags (DNSSEC) mistaken for a flag"),
    ("密钥标签", "钥匙标签", "key tag (DNSSEC) mistaken for a key ring tag"),
    ("密钥记录", "关键记录", "key records (DNSSEC) mistaken for key records"),
    ("记录", "唱片", "records (DNS) mistaken for music records"),
    ("充值", "加值", "add funds mistaken for value-added"),
    ("TXT (文本)", "防晒 (纯色素)", "TXT record garbled as SPF / pigment"),
    ("POP3", "流行音乐 3", "POP3 mistaken for pop music 3"),
    ("Markdown 指南", "降价指南", "Markdown mistaken for price reduction"),
    ("史密斯", "工匠", "Smith (sample surname) mistaken for smith/craftsman"),
    ("中断", "停电", "outage mistaken for a power cut"),
    ("已计划", "已排程", "scheduled mixed with the Taiwan form 排程"),
    ("未运行", "不运行", "not running rendered as 'does not run'"),
    ("DJ", "唱片骑师", "DJ expanded into a literal disc jockey"),
    ("开启时间", "开业时间", "opened at (ticket) mistaken for business opening"),
    ("反馈", "意见书", "feedback mistaken for a written opinion"),
    ("双年", "双年展", "biennially mistaken for a biennale (art exhibition)"),
    ("延长", "展延", "extend mixed with the Taiwan form 展延"),
]

# Phrases that are wrong anywhere they appear.
SUBSTRING_BAD = [
    ("工单", "门票", "ticket"),
    ("工单", "机票", "ticket"),
    ("分钟", "会议纪要", "minutes"),
    ("表格", "桌子", "tables"),
    ("粗体", "大胆", "bold"),
    ("运行", "跑步", "running"),
    ("标志", "旗帜", "flags"),
    ("密钥", "钥匙标签", "key tag"),
    ("充值", "加值", "add funds"),
    ("已计划", "已排程", "scheduled"),
    ("时间线", "时间轴", "timeline"),
    ("延期", "展延", "extend"),
]

# Fixed project terminology, matched against the last key segment.
GLOSSARY = [
    ("apply", "应用", "Apply button"),
    ("applyPromoCode", "应用优惠码", "Apply promo code"),
    ("addfunds", "充值", "Add funds"),
    ("domainDns.txt", "TXT (文本)", "DNS TXT record"),
    ("domainDns.a", "A (地址)", "DNS A record"),
    ("domainDns.aaaa", "AAAA (地址)", "DNS AAAA record"),
    ("domainDns.cname", "CNAME (别名)", "DNS CNAME record"),
    ("domainDns.mx", "MX (邮件)", "DNS MX record"),
    ("domainDns.mxe", "MXE (简易邮件)", "DNS MXE record"),
    # 月缴 is correct for the billing-term adjective; only the VPS panel label
    # needs the plain "every month" reading.
    ("vpsnetmonthly", "每月", "VPS panel: monthly"),
]

# Advisory: a pure-ASCII abbreviation that got expanded into prose. Such values
# are almost always homograph damage (POP3 -> 流行音乐 3).
ABBREV_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


def load_pattern(pat: str):
    return re.compile(pat)


def key_path(keys: str) -> str:
    return ".".join(KEY_RX.findall(keys))


def normalize(value: str) -> str:
    """Trim spaces and a trailing sentence period so '分钟。' still matches."""
    v = value.strip()
    return v[:-1].rstrip() if v.endswith("。") else v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dst")
    ap.add_argument("--report")
    ap.add_argument("--pattern", default=RX.pattern)
    ap.add_argument("--extra", help="JSON file with additional rules")
    args = ap.parse_args(argv)

    exact = list(EXACT_BAD)
    substr = list(SUBSTRING_BAD)
    glossary = list(GLOSSARY)
    if args.extra:
        with open(args.extra, encoding="utf-8") as fh:
            for rule in json.load(fh):
                t = rule.get("type")
                if t == "exact":
                    exact.append((rule["good"], rule["bad"], rule.get("note", "")))
                elif t == "substring":
                    substr.append((rule["good"], rule["bad"], rule.get("note", "")))
                elif t == "glossary":
                    glossary.append((rule["key"], rule["good"], rule.get("note", "")))
                else:
                    print(f"warning: unknown rule type {t!r}", file=sys.stderr)

    rx = load_pattern(args.pattern)
    try:
        with open(args.dst, "r", encoding="utf-8", newline="") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out: list[str] = []

    def emit(s: str = "") -> None:
        out.append(s)
        print(s)

    problems: list[tuple[int, str, str, str]] = []
    advisories: list[tuple[int, str, str]] = []

    for i, line in enumerate(lines, 1):
        m = rx.match(line)
        if not m:
            continue
        value = m.group("value")
        path = key_path(m.group("keys"))
        plain = normalize(value)

        for good, bad, note in exact:
            if plain == bad:
                problems.append((i, bad, good, note))

        for good, bad, note in substr:
            if bad in value and all(plain != b for _, b, _ in exact):
                # SUBSTRING_BAD is advisory when the replacement is a normal word
                # in another context ("申请" is legitimate in 申请退款).
                advisories.append((i, bad, f"{good}  ({note})"))

        for key, expected, note in glossary:
            if path == key and plain != expected:
                problems.append((i, plain, expected, note))

    emit(f"file: {args.dst}  ({len(lines)} lines)")
    emit("")
    emit("=== hard: known wrong renderings (value-level) ===")
    if not problems:
        emit("  none")
    reported: set[tuple[int, str]] = set()
    for ln, found, expected, note in problems:
        # EXACT_BAD and GLOSSARY can both flag the same line; report it once.
        if (ln, expected) in reported:
            continue
        reported.add((ln, expected))
        emit(f"  L{ln}: {found!r} -> {expected!r}   [{note}]")

    emit("")
    emit("=== advisory: suspect phrases (adjudicate each) ===")
    if not advisories:
        emit("  none")
    seen: set[tuple[int, str]] = set()
    for ln, bad, note in advisories:
        if (ln, bad) in seen:
            continue
        seen.add((ln, bad))
        emit(f"  L{ln}: contains {bad!r}  -> check for {note}")

    emit("")
    emit(f"hard problems: {len(reported)}   advisories: {len(seen)}")

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(out) + "\n")
        emit(f"report written to {args.report}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
