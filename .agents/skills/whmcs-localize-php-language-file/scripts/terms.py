#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""terms.py - catch competing vocabulary that `langtool candidates` cannot see.

`candidates` groups by EXACT source string, so it only finds entries whose
zh_TW/English source is byte-identical. But two different source strings can
mean the same thing (兩種「Ticket」寫法, 兩種「Add」寫法), and then the
translations diverge with no alarm at all. This script counts both sides of a
list of term pairs across the whole file and prints every line where the
non-preferred side survived.

Usage
-----
  python terms.py chinese.php [--report terms.txt]
  python terms.py chinese.php --pairs-file mypairs.json

Pairs file format (JSON list of 3-element arrays):
  [["工单", "服务单", "Ticket"], ["账单", "发票", "Invoice"]]

Built-in default pairs target zh_TW → zh_CN (WHMCS/Laravel vocabulary).
Exit codes: 0 no competing form / 1 competing form present.
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

RX = re.compile(r"""^(?P<prefix>\s*\$[A-Za-z_][A-Za-z0-9_]*\s*(?:\[[^\]]*\]\s*)*=\s*)"(?P<value>.*)";\s*$""")

# (preferred mainland form, competing Taiwan form, note)
DEFAULT_PAIRS = [
    ("工单", "服务单", "Ticket"),
    ("账单", "发票", "Invoice"),
    ("附加组件", "附加套件", "Addon"),
    ("附加组件", "外挂", "Addon/Plugin"),
    ("域名", "网域", "Domain"),
    ("用户", "使用者", "Client/User"),
    ("默认", "预设", "Default"),
    ("设置", "设定", "Setting"),
    ("登录", "登入", "Login"),
    ("保存", "储存", "Save"),
    ("信息", "资讯", "Information"),
    ("服务器", "伺服器", "Server"),
    ("添加", "新增", "Add"),
    ("获取", "取得", "Get"),
    ("支持", "支援", "Support"),
    ("网络", "网路", "Network"),
    ("缓存", "快取", "Cache"),
    ("端口", "连接埠", "Port"),
    ("性能", "效能", "Performance"),
    ("质量", "品质", "Quality"),
    ("兼容", "相容", "Compatible"),
    ("优化", "最佳化", "Optimize"),
    ("自定义", "自订", "Custom"),
    ("超时", "逾时", "Timeout"),
    ("密钥", "金钥", "Key"),
    ("网关", "闸道", "Gateway"),
    ("模块", "模组", "Module"),
    ("搜索", "搜寻", "Search"),
    ("刷新", "重新整理", "Refresh"),
    ("卸载", "解除安装", "Uninstall"),
    ("打印", "列印", "Print"),
    ("屏幕", "萤幕", "Screen"),
    ("分辨率", "解析度", "Resolution"),
    ("视频", "影片", "Video"),
    ("照片", "相片", "Photo"),
    ("菜单", "选单", "Menu"),
    ("界面", "介面", "Interface"),
    ("模板", "范本", "Template"),
    ("访问", "存取", "Access"),
    ("消息", "讯息", "Message"),
    ("队列", "伫列", "Queue"),
    ("带宽", "频宽", "Bandwidth"),
    ("币种", "币别", "Currency"),
    ("邮政编码", "邮递区号", "Postcode"),
    ("疑难解答", "疑难排解", "Troubleshoot"),
    ("查看", "检视", "View"),
    ("字段", "栏位", "Field"),
    ("窗口", "视窗", "Window"),
    # advisory: both sides are standard mainland usage, listed only for
    # consistency checking -- keep the pair if you want them unified.
    ("数据", "资料", "Data(advisory)"),
    ("文件", "档案", "File(advisory)"),
    ("证书", "凭证", "Certificate(advisory)"),
    ("创建", "建立", "Create(advisory)"),
    ("项目", "专案", "Project(advisory)"),
    ("迁移", "移转", "Migrate(advisory)"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dst")
    ap.add_argument("--report")
    ap.add_argument("--show", type=int, default=200)
    ap.add_argument("--pattern", default=RX.pattern)
    ap.add_argument("--pairs-file", help="JSON list of [preferred, competing, note]")
    args = ap.parse_args(argv)

    pairs = [tuple(p) for p in DEFAULT_PAIRS]
    if args.pairs_file:
        with open(args.pairs_file, encoding="utf-8") as fh:
            pairs += [tuple(p) for p in json.load(fh)]

    rx = re.compile(args.pattern)
    with open(args.dst, "r", encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines()

    out: list[str] = []

    def emit(s=""):
        out.append(s)
        print(s)

    hits: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
    counts: dict[str, int] = {}
    for i, line in enumerate(lines, 1):
        m = rx.match(line)
        if not m:
            continue
        v = m.group("value")
        for good, bad, note in pairs:
            if good in v:
                counts[good] = counts.get(good, 0) + 1
            if bad in v:
                counts[bad] = counts.get(bad, 0) + 1
                hits.setdefault((bad, good, note), []).append((i, v))

    emit(f"file: {args.dst}  ({len(lines)} lines)")
    emit("")
    emit("=== preferred vs competing form (occurrence counts) ===")
    for good, bad, note in pairs:
        g, b = counts.get(good, 0), counts.get(bad, 0)
        flag = "  <-- competing form present" if b else ""
        emit(f"  {good:<10} {g:>4}   |  {bad:<10} {b:>4}   ({note}){flag}")

    emit("")
    emit("=== lines where the competing form survived (adjudicate each) ===")
    total = 0
    for (bad, good, note), items in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        emit(f"\n-- {bad} (prefer {good})  [{note}]  {len(items)} lines")
        total += len(items)
        for ln, v in items[: args.show]:
            emit(f"   L{ln}: {v[:150]}")
        if len(items) > args.show:
            emit(f"   ... and {len(items) - args.show} more")

    emit("")
    emit(f"total competing-form occurrences: {total}")

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(out) + "\n")
        emit(f"report written to {args.report}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
