#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
langtool.py - lossless localization harness for PHP language-array files.

Built for files shaped like one complete assignment per line:

    $_ADMINLANG['group']['key'] = "English text";
    $_ADMINLANG['group']['key'] = "Text with <a href=\\"page.php\\">a link</a> and :placeholder";

The whole point of this tool is to make "did the translation damage the file?"
a question with an objective answer instead of a matter of opinion.

Subcommands
-----------
  preflight   Decide whether the file can be safely split, and inventory the
              tokens that translation must not disturb.
  split       Cut the source into fixed-size line chunks.
  assemble    Concatenate translated chunks back into one file.
  validate    Compare source vs translation across structural invariants.
  normalize   Force one canonical rendering per distinct source string.
  lexcheck    Per-line lexical sanity check of PHP string literals.

Typical run
-----------
  python langtool.py preflight english.php
  python langtool.py split     english.php --size 300 --outdir .work/in
  # ... translate .work/in/chunk_NN.txt -> .work/out/chunk_NN.txt ...
  python langtool.py assemble  .work/out --dst chinese.php
  python langtool.py validate  english.php chinese.php --report .work/report.txt
  python langtool.py normalize english.php chinese.php --map canon.json
  python langtool.py lexcheck  chinese.php

Exit codes: 0 = clean, 1 = problems found, 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict


def _force_utf8_console() -> None:
    """Windows consoles often default to a legacy codepage (GBK, cp1252) that
    cannot represent the very characters this tool reports on. Reconfigure the
    streams so report output never dies mid-run on an encoding error."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_force_utf8_console()

# --------------------------------------------------------------------------
# Line grammar
# --------------------------------------------------------------------------

# Default entry grammar: a PHP variable, optional [..] subscripts, '=', then a
# double-quoted value terminated by "; at end of line. Greedy value capture is
# deliberate: the value ends at the LAST  ";  on the line, which is exactly how
# PHP itself would terminate it, and it tolerates embedded \" escapes.
DEFAULT_PATTERN = r"""^(?P<prefix>\s*\$[A-Za-z_][A-Za-z0-9_]*\s*(?:\[[^\]]*\]\s*)*=\s*)"(?P<value>.*)";\s*$"""

# Tokens that must survive translation untouched, compared as multisets.
PLACEHOLDER_RE = re.compile(r"%[a-zA-Z]|:[a-zA-Z][a-zA-Z0-9_]*|\{[a-zA-Z][a-zA-Z0-9_]*\}")
ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")
TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# URLs: stop at whitespace, quote, bracket, and any CJK/full-width punctuation
# so a trailing Chinese ） or 。 is not swallowed into the URL.
URL_RE = re.compile(r"https?://[^\s\"'<>)\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]+|[\w\-/]+\.php\b")
ESCAPED_QUOTE_RE = re.compile(r'\\"')
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def parse_entry(line: str, rx: re.Pattern):
    """Return (prefix, value) if the line is a translatable entry, else None."""
    m = rx.match(line)
    if not m:
        return None
    return m.group("prefix"), m.group("value")


def rebuild(prefix: str, value: str) -> str:
    """Recompose a translated entry from its prefix and new value."""
    return f'{prefix}"{value}";'


def multiset(rx: re.Pattern, text: str) -> list[str]:
    return sorted(rx.findall(text))


def read_lines(path: str) -> tuple[list[str], str]:
    """Read UTF-8 text, preserving the original newline convention."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    # splitlines() drops the terminator; we re-join with the detected one.
    return raw.splitlines(), nl


def write_lines(path: str, lines: list[str], nl: str = "\n") -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(nl.join(lines))
        fh.write(nl)


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------

def cmd_preflight(args) -> int:
    rx = re.compile(args.pattern)
    lines, nl = read_lines(args.src)

    entries, odd = [], []
    for i, line in enumerate(lines, 1):
        parsed = parse_entry(line, rx)
        if parsed:
            entries.append((i, line, parsed))
        elif line.strip() == "" or line.strip() in ("<?php", "?>"):
            odd.append((i, line, "benign"))
        else:
            odd.append((i, line, "UNRECOGNIZED"))

    hard = [o for o in odd if o[2] == "UNRECOGNIZED"]

    print(f"file            : {args.src}")
    print(f"total lines     : {len(lines)}")
    print(f"newline         : {'CRLF' if nl == chr(13) + chr(10) else 'LF'}")
    print(f"translatable    : {len(entries)}")
    print(f"benign non-entry: {len(odd) - len(hard)}  (blank / <?php)")
    print(f"UNRECOGNIZED    : {len(hard)}")

    if hard:
        print("\n!! Lines that do not match the entry grammar.")
        print("!! Line-based chunking is UNSAFE until these are understood.")
        print("!! A multi-line string split arbitrarily across chunks will corrupt the file.")
        for i, line, _ in hard[: args.show]:
            print(f"   L{i}: {line[:160]}")
        if len(hard) > args.show:
            print(f"   ... and {len(hard) - args.show} more")
    else:
        print("\nOK: every non-blank line is a self-contained entry.")
        print("    Safe to split at any line boundary.")

    # Token inventory
    joined = "\n".join(v for _, _, (_, v) in entries)
    inv = OrderedDict(
        (
            ("escaped quotes \\\"", Counter(ESCAPED_QUOTE_RE.findall(joined))),
            ("placeholders", Counter(PLACEHOLDER_RE.findall(joined))),
            ("html entities", Counter(ENTITY_RE.findall(joined))),
            ("html tags", Counter(TAG_RE.findall(joined))),
            ("urls / paths", Counter(URL_RE.findall(joined))),
        )
    )
    print("\ntoken inventory (these must survive translation):")
    for name, counter in inv.items():
        total = sum(counter.values())
        sample = ", ".join(sorted(counter)[: args.show])
        print(f"  {name:<20} {total:>6}   {sample}")

    esc_lines = [(i, v) for i, _, (_, v) in entries if ESCAPED_QUOTE_RE.search(v)]
    print(f"\nlines containing \\\" escapes: {len(esc_lines)}")
    if esc_lines:
        print("  These are the highest-risk lines. Audit them by count parity after")
        print("  translation: \"  must never become a typographic quotation mark.")
        for i, v in esc_lines[:5]:
            print(f"   L{i}: {v[:120]}")
        if len(esc_lines) > 5:
            print(f"   ... and {len(esc_lines) - 5} more")

    return 1 if hard else 0


# --------------------------------------------------------------------------
# split / assemble
# --------------------------------------------------------------------------

def cmd_split(args) -> int:
    lines, _ = read_lines(args.src)
    os.makedirs(args.outdir, exist_ok=True)
    digits = max(2, len(str((len(lines) // args.size) + 1)))
    manifest = []
    n = 0
    for start in range(0, len(lines), args.size):
        chunk = lines[start : start + args.size]
        n += 1
        name = f"chunk_{n:0{digits}d}.txt"
        write_lines(os.path.join(args.outdir, name), chunk, "\n")
        manifest.append({"file": name, "lines": len(chunk), "start": start + 1})
    write_lines(
        os.path.join(args.outdir, "_manifest.json"),
        [json.dumps({"src": os.path.abspath(args.src), "total": len(lines), "chunks": manifest}, indent=2)],
        "\n",
    )
    print(f"wrote {n} chunks of <= {args.size} lines to {args.outdir}")
    print(f"total lines covered: {len(lines)}")
    return 0


def cmd_assemble(args) -> int:
    files = sorted(
        f for f in os.listdir(args.chunkdir)
        if re.match(r"^chunk_\d+\.txt$", f)
    )
    if not files:
        print(f"no chunk_*.txt found in {args.chunkdir}", file=sys.stderr)
        return 2
    all_lines, expected = [], 0
    for f in files:
        chunk, nl = read_lines(os.path.join(args.chunkdir, f))
        all_lines.extend(chunk)
        print(f"  {f}: {len(chunk)} lines")
        expected += len(chunk)
    write_lines(args.dst, all_lines, nl)
    print(f"assembled {len(files)} chunks -> {args.dst} ({expected} lines)")

    manifest_path = os.path.join(args.chunkdir, "_manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            man = json.load(fh)
        if man["total"] != expected:
            print(f"!! line count drift: manifest says {man['total']}, assembled {expected}")
            return 1
        print(f"line count matches manifest ({man['total']})")
    return 0


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def cmd_validate(args) -> int:
    rx = re.compile(args.pattern)
    src, _ = read_lines(args.src)
    dst, _ = read_lines(args.dst)

    out = []
    def emit(s=""):
        out.append(s)
        if not args.quiet:
            print(s)

    emit(f"source : {args.src}  ({len(src)} lines)")
    emit(f"output : {args.dst}  ({len(dst)} lines)")

    hard, warn = [], []

    if len(src) != len(dst):
        hard.append(f"LINE COUNT: source has {len(src)}, output has {len(dst)}")

    esc_bad, ph_bad, ent_bad, tag_bad, url_bad, key_bad = [], [], [], [], [], []
    changed_values, unchanged_prose = [], []

    for idx in range(min(len(src), len(dst))):
        s, d = src[idx], dst[idx]
        ln = idx + 1
        ps, pd = parse_entry(s, rx), parse_entry(d, rx)

        if ps is None and pd is None:
            if s != d:
                key_bad.append((ln, "non-entry line altered", s, d))
            continue
        if (ps is None) != (pd is None):
            key_bad.append((ln, "entry/non-entry mismatch", s, d))
            continue

        sp, sv = ps
        dp, dv = pd
        if sp != dp:
            key_bad.append((ln, "key or prefix changed", sp, dp))

        # escapes: count parity is the strongest cheap signal of broken PHP
        if ESCAPED_QUOTE_RE.findall(sv).__len__() != ESCAPED_QUOTE_RE.findall(dv).__len__():
            esc_bad.append((ln, sv, dv))

        if multiset(PLACEHOLDER_RE, sv) != multiset(PLACEHOLDER_RE, dv):
            ph_bad.append((ln, sv, dv))
        if multiset(ENTITY_RE, sv) != multiset(ENTITY_RE, dv):
            ent_bad.append((ln, sv, dv))
        if TAG_RE.findall(sv) != TAG_RE.findall(dv):
            tag_bad.append((ln, sv, dv))
        if multiset(URL_RE, sv) != multiset(URL_RE, dv):
            url_bad.append((ln, sv, dv))

        if sv != dv:
            changed_values.append(ln)
        elif CJK_RE.search(sv) is None and len(re.findall(r"\b[A-Za-z]{2,}\b", sv)) >= 2:
            unchanged_prose.append((ln, sv))

    def section(title, items, detail=True):
        if not items:
            emit(f"  {title:<26} 0")
            return
        emit(f"  {title:<26} {len(items)}")
        hard.append(f"{title}: {len(items)}")
        if detail and not args.quiet:
            for it in items[: args.show]:
                if len(it) == 4:
                    emit(f"     L{it[0]}: {it[1]}\n        -> {it[3]}")
                else:
                    emit(f"     L{it[0]}: {it[1][:150]}\n        -> {it[2][:150]}")
            if len(items) > args.show:
                emit(f"     ... and {len(items) - args.show} more")

    emit("\n=== hard invariants (must be 0) ===")
    section("key / structure changed", key_bad)
    section("escaped-quote count differ", esc_bad)
    section("placeholder set differ", ph_bad)
    section("html tag sequence differ", tag_bad)
    section("url / path set differ", url_bad)
    if args.strict_entities:
        section("html entity set differ", ent_bad)
    else:
        emit(f"  {'html entity set differ':<26} {len(ent_bad)}   (advisory, see below)")

    # Entities are advisory by default: &amp; used as the English conjunction
    # "and" is legitimately absorbed in many target languages.
    if ent_bad:
        emit("\n  -- entity differences (adjudicate, do not auto-fix) --")
        for ln, sv, dv in ent_bad[: args.show]:
            emit(f"     L{ln}: {sv[:130]}")
            emit(f"        -> {dv[:130]}")
        if len(ent_bad) > args.show:
            emit(f"     ... and {len(ent_bad) - args.show} more")

    emit("\n=== summary ===")
    emit(f"  values changed                : {len(changed_values)}")
    emit(f"  values left identical         : {len(src) - len(changed_values)}")
    emit(f"  possibly untranslated prose   : {len(unchanged_prose)}")
    emit(f"  hard problems                 : {len(hard)}")

    if unchanged_prose and not args.quiet:
        emit("\n  -- English-looking values (verify these are brand names / URLs) --")
        for ln, sv in unchanged_prose[: args.show]:
            emit(f"     L{ln}: {sv[:130]}")
        if len(unchanged_prose) > args.show:
            emit(f"     ... and {len(unchanged_prose) - args.show} more")

    if args.report:
        write_lines(args.report, out, "\n")
        emit(f"\nreport written to {args.report}")

    return 1 if hard else 0


# --------------------------------------------------------------------------
# normalize  (cross-chunk terminology consistency)
# --------------------------------------------------------------------------

def cmd_normalize(args) -> int:
    rx = re.compile(args.pattern)
    src, nl = read_lines(args.src)
    dst, _ = read_lines(args.dst)

    overrides = {}
    if args.map:
        with open(args.map, encoding="utf-8") as fh:
            overrides = json.load(fh)

    # group output line indices by the EXACT source value
    groups: "OrderedDict[str, list[int]]" = OrderedDict()
    for idx, s in enumerate(src):
        parsed = parse_entry(s, rx)
        if not parsed:
            continue
        sv = parsed[1]
        if not sv.strip():
            continue
        groups.setdefault(sv, []).append(idx)

    changed, skipped, unresolved = [], [], []

    for sv, idxs in groups.items():
        if len(idxs) < 2:
            continue
        current = {}
        for i in idxs:
            pd = parse_entry(dst[i], rx)
            if pd:
                current.setdefault(pd[1], []).append(i)
        if len(current) < 2:
            continue  # already consistent

        if sv in overrides:
            target = overrides[sv]
        else:
            # default: most frequent existing rendering, deterministic tiebreak
            target = sorted(current.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0][0]

        if args.report_only:
            unresolved.append((sv, list(current), target))
            continue

        tgt_ph = multiset(PLACEHOLDER_RE, target)
        tgt_esc = ESCAPED_QUOTE_RE.findall(target).__len__()

        for value, lines_with_value in current.items():
            if value == target:
                continue
            # safety rail: never overwrite a value whose token signature differs
            # from the canonical one, or we would silently change meaning/structure.
            if multiset(PLACEHOLDER_RE, value) != tgt_ph or \
               ESCAPED_QUOTE_RE.findall(value).__len__() != tgt_esc:
                skipped.append((sv, value, target))
                continue
            for i in lines_with_value:
                pd = parse_entry(dst[i], rx)
                dst[i] = rebuild(pd[0], target)
                changed.append((i + 1, sv, value, target))

    if args.report_only:
        print(f"inconsistent source strings: {len(unresolved)}")
        for sv, variants, target in unresolved:
            print(f"\nSRC: {sv}")
            for v, ls in variants:
                mark = "  <== canonical" if v == target else ""
                print(f"   [{','.join('L'+str(x+1) for x in ls)}] {v}{mark}")
        return 1 if unresolved else 0

    if changed:
        write_lines(args.dst, dst, nl)
    print(f"inconsistent source strings : {len({c[1] for c in changed})}")
    print(f"lines rewritten             : {len(changed)}")
    print(f"skipped (token mismatch)    : {len(skipped)}")
    if skipped:
        print("  -- skipped, canonical form had a different token signature --")
        for sv, v, t in skipped[: args.show]:
            print(f"     '{sv[:80]}'\n        kept: {v[:80]}\n        want: {t[:80]}")
    if args.log:
        write_lines(
            args.log,
            [f"L{a}: '{b}' : '{c}' -> '{d}'" for a, b, c, d in changed]
            + [f"SKIP: '{b}' : keep '{c}' (wanted '{d}')" for b, c, d in skipped],
            "\n",
        )
        print(f"change log written to {args.log}")
    return 0


def cmd_candidates(args) -> int:
    """Emit the map of duplicated source strings -> {rendering: [lines]} as JSON,
    so a human (or an LLM) can pick canonical terms deliberately."""
    rx = re.compile(args.pattern)
    src, _ = read_lines(args.src)
    dst, _ = read_lines(args.dst)
    groups = {}
    for idx, s in enumerate(src):
        ps, pd = parse_entry(s, rx), parse_entry(dst[idx], rx)
        if not ps or not pd:
            continue
        groups.setdefault(ps[1], {}).setdefault(pd[1], []).append(idx + 1)
    divergent = {k: v for k, v in groups.items() if len(v) > 1}
    print(json.dumps(divergent, ensure_ascii=False, indent=2))
    return 1 if divergent else 0


# --------------------------------------------------------------------------
# lexcheck  (PHP string literal sanity, line by line)
# --------------------------------------------------------------------------

def cmd_lexcheck(args) -> int:
    rx = re.compile(args.pattern)
    lines, _ = read_lines(args.src)
    bad = []
    ok = 0
    for i, line in enumerate(lines, 1):
        if not line.strip():
            ok += 1
            continue
        if parse_entry(line, rx) is None:
            if line.strip() in ("<?php", "?>"):
                ok += 1
            else:
                bad.append((i, "does not match entry grammar", line))
            continue
        # walk the literal character by character, honouring backslash escapes
        start = line.index('"')
        j, closed = start + 1, -1
        while j < len(line):
            ch = line[j]
            if ch == "\\":
                j += 2
                continue
            if ch == '"':
                closed = j
                break
            j += 1
        if closed < 0:
            bad.append((i, "unterminated string literal", line))
            continue
        tail = line[closed + 1 :]
        if tail != ";":
            bad.append((i, f"unexpected text after closing quote: {tail!r}", line))
            continue
        ok += 1

    print(f"lines checked : {len(lines)}")
    print(f"lines ok      : {ok}")
    print(f"lines bad     : {len(bad)}")
    for i, why, line in bad[: args.show]:
        print(f"  L{i}: {why}\n     {line[:160]}")
    if len(bad) > args.show:
        print(f"  ... and {len(bad) - args.show} more")
    if not bad:
        print("\nOK: every string literal is properly quoted, escaped and terminated.")
        print("    (This is a lexical check, not a substitute for `php -l`.)")
    return 1 if bad else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="langtool",
        description="Lossless localization harness for PHP language-array files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Shared options are attached to every subcommand so they may be written
    # after the verb (the natural place) as well as before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--pattern", default=DEFAULT_PATTERN,
                        help="regex with named groups prefix/value describing one entry")
    common.add_argument("--show", type=int, default=15,
                        help="detail rows to print (default 15)")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("preflight", parents=[common], help="assess chunkability + inventory tokens")
    s.add_argument("src")
    s.set_defaults(func=cmd_preflight)

    s = sub.add_parser("split", parents=[common], help="split source into line chunks")
    s.add_argument("src")
    s.add_argument("--size", type=int, default=300)
    s.add_argument("--outdir", required=True)
    s.set_defaults(func=cmd_split)

    s = sub.add_parser("assemble", parents=[common], help="concatenate translated chunks")
    s.add_argument("chunkdir")
    s.add_argument("--dst", required=True)
    s.set_defaults(func=cmd_assemble)

    s = sub.add_parser("validate", parents=[common], help="compare source vs translation")
    s.add_argument("src")
    s.add_argument("dst")
    s.add_argument("--report")
    s.add_argument("--strict-entities", action="store_true",
                   help="treat HTML entity differences as hard failures")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("normalize", parents=[common], help="force one rendering per source string")
    s.add_argument("src")
    s.add_argument("dst")
    s.add_argument("--map", help="JSON {source: canonical} overrides")
    s.add_argument("--log")
    s.add_argument("--report-only", action="store_true")
    s.set_defaults(func=cmd_normalize)

    s = sub.add_parser("candidates", parents=[common], help="print divergent source->renderings as JSON")
    s.add_argument("src")
    s.add_argument("dst")
    s.set_defaults(func=cmd_candidates)

    s = sub.add_parser("lexcheck", parents=[common], help="lexical PHP string-literal check")
    s.add_argument("src")
    s.set_defaults(func=cmd_lexcheck)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
