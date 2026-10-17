#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""residue.py - independent *language* checks that langtool.py does not do.

langtool.py checks structure (keys, escapes, placeholders, tags, urls).
This script checks language. It exists for one specific situation: translating
between two scripts of the SAME language (e.g. zh_TW 繁體 → zh_CN 简体). There,
"did the translator actually convert everything?" is invisible to a structural
check, and the translators' own scripts are the worst place to look for the
answer -- in a verified run, 8 subagents reported "0 traditional characters
left" while an independent check found 現/窺/齊 still sitting in the file.

Checks
------
  1. residual Traditional-only characters (needs OpenCC)
  2. Taiwan-isms that are valid Simplified characters, so OpenCC cannot see them
     (设定, 网路, 使用者 ...) -- advisory, adjudicate each hit
  3. whole-string values with no CJK that were modified anyway
     (contract rule: pure-English values must stay byte-identical)

Usage
-----
  python residue.py chinese_zh_tw.php chinese.php [--report out.txt]

OpenCC setup -- pip is often blocked by a file sandbox, and then
`pip install` dies with a permission error inside its own unpack directory.
Fetch the pure-python wheel and unzip it instead, then point this script at it:

  python -c "import json,urllib.request,zipfile,io; \
    m=json.load(urllib.request.urlopen('https://pypi.org/pypi/opencc-python-reimplemented/json')); \
    u=[x for x in m['urls'] if x['filename'].endswith('.whl')][0]; \
    zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u['url']).read())).extractall('.work/pylibs')"

  set OPENCC_PYTHONPATH=.work/pylibs        (Windows)
  export OPENCC_PYTHONPATH=.work/pylibs     (POSIX)

Exit codes: 0 clean / 1 hard problem / 2 usage or IO error.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

# Allow a locally unpacked OpenCC to be pointed at without touching the host.
for _p in filter(None, [os.environ.get("OPENCC_PYTHONPATH")]):
    sys.path.insert(0, _p)

try:
    from opencc import OpenCC
    _T2S = OpenCC("t2s")
except Exception as _exc:  # pragma: no cover
    _T2S = None
    _OPENCC_ERR = _exc
else:
    _OPENCC_ERR = None


def force_utf8() -> None:
    """Windows consoles still default to legacy codepages; never die on output."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


force_utf8()

DEFAULT_PATTERN = r"""^(?P<prefix>\s*\$[A-Za-z_][A-Za-z0-9_]*\s*(?:\[[^\]]*\]\s*)*=\s*)"(?P<value>.*)";\s*$"""
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# Taiwan-isms that survive as perfectly valid Simplified characters, so the
# OpenCC single-character test cannot detect them. These MUST be written in
# Simplified script -- that is what a half-converted file contains.
# Traditional-script forms are already caught by the character check.
# Override/extend with --pairs-file (JSON list of strings).
DEFAULT_TAIWAN_WORDS = [
    "网际网路", "伺服器", "网路", "网域", "设定", "预设", "选单", "介面",
    "范本", "外挂", "模组", "资料库", "资料夹", "资料", "资讯", "档案",
    "使用者", "帐号", "发票", "服务单", "闸道", "金钥", "凭证", "连接埠",
    "通讯埠", "伫列", "快取", "频宽", "效能", "品质", "相容", "最佳化",
    "自订", "逾时", "税金", "币别", "简讯", "邮递区号", "统一编号",
    "身分证", "批次", "精灵", "重新整理", "解除安装", "移转", "存取",
    "登出", "储存", "载入", "汇入", "汇出", "搜寻", "点选", "停用",
    "支援", "连线", "签章", "透过", "列印", "重新启动", "萤幕",
    "解析度", "影片", "相片", "隐私权政策", "疑难排解", "检视", "栏位",
    "视窗", "专案", "丛集", "阵列", "位址", "讯息",
]
# NOTE: words like 目前 / 停用 / 资料 / 建立 / 取得 are deliberately NOT in the
# default list for zh_CN -- they are standard mainland usage too, and flagging
# them drowns the real hits in noise. Keep the list to genuine divergences.


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--report")
    ap.add_argument("--show", type=int, default=25)
    ap.add_argument("--pattern", default=DEFAULT_PATTERN)
    ap.add_argument("--pairs-file", help="JSON list of extra competing-form words to police")
    args = ap.parse_args(argv)

    rx = re.compile(args.pattern)
    words = list(DEFAULT_TAIWAN_WORDS)
    if args.pairs_file:
        import json
        with open(args.pairs_file, encoding="utf-8") as fh:
            words += json.load(fh)

    with open(args.src, "r", encoding="utf-8", newline="") as fh:
        src = fh.read().splitlines()
    with open(args.dst, "r", encoding="utf-8", newline="") as fh:
        dst = fh.read().splitlines()

    out: list[str] = []

    def emit(s=""):
        out.append(s)
        print(s)

    emit(f"source : {args.src} ({len(src)} lines)")
    emit(f"output : {args.dst} ({len(dst)} lines)")
    emit(f"opencc : {'available' if _T2S else 'MISSING: ' + str(_OPENCC_ERR)}")

    trad, tw, eng_changed = [], [], []
    changed = pairs = 0

    for i in range(min(len(src), len(dst))):
        ms, md = rx.match(src[i]), rx.match(dst[i])
        if not ms or not md:
            continue
        pairs += 1
        sv, dv = ms.group("value"), md.group("value")
        if sv != dv:
            changed += 1
        ln = i + 1

        if _T2S:
            bad = sorted({ch for ch in dv if ch.strip() and _T2S.convert(ch) != ch})
            if bad:
                trad.append((ln, dv, bad))

        hits = sorted({w for w in words if w in dv}, key=len, reverse=True)
        if hits:
            # drop a word fully contained in a longer hit (存取 inside 存取權)
            tw.append((ln, dv, [w for w in hits if not any(w != o and w in o for o in hits)]))

        if not CJK_RE.search(sv) and sv != dv:
            eng_changed.append((ln, sv, dv))

    emit(f"entry pairs compared          : {pairs}")
    emit(f"values changed                : {changed}")
    emit("")
    emit("=== hard ===")
    emit(f"  residual traditional chars    : {len(trad)}")
    for ln, dv, bad in trad[: args.show]:
        emit(f"    L{ln}: [{''.join(bad)}]  {dv[:120]}")
    if len(trad) > args.show:
        emit(f"    ... and {len(trad) - args.show} more")

    emit(f"  pure-English values modified  : {len(eng_changed)}")
    for ln, sv, dv in eng_changed[: args.show]:
        emit(f"    L{ln}: {sv[:110]}")
        emit(f"        -> {dv[:110]}")
    if len(eng_changed) > args.show:
        emit(f"    ... and {len(eng_changed) - args.show} more")

    emit("")
    emit("=== advisory: competing vocabulary still present (adjudicate) ===")
    emit(f"  lines with competing terms    : {len(tw)}")
    counts = Counter(w for _, _, ws in tw for w in ws)
    emit("  frequency: " + ", ".join(f"{w}x{c}" for w, c in counts.most_common(40)))
    for ln, dv, ws in tw[: args.show]:
        emit(f"    L{ln}: [{','.join(ws)}]  {dv[:110]}")
    if len(tw) > args.show:
        emit(f"    ... and {len(tw) - args.show} more")

    hard = len(trad) + len(eng_changed)
    emit("")
    emit(f"hard problems (traditional + english modified): {hard}")

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(out) + "\n")
        emit(f"report written to {args.report}")

    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
