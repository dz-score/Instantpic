#!/usr/bin/env python3
"""
check_duplication — find prose that is written down in more than one place.

The mechanical half of BACKEND_RULES Rule 25. Extracts every comment, docstring
and paragraph in the repo, reduces each to overlapping word-shingles, and reports
any shingle living in two or more files.

    # everything, worst first
    python3 backend/tools/check_duplication.py

    # only what a branch introduced, with the same scan at main subtracted
    python3 backend/tools/check_duplication.py --since main

    # what is staged right now, as the pre-commit hook runs it
    python3 backend/tools/check_duplication.py --staged --since main --width 4

    # the shared text between two specific files
    python3 backend/tools/check_duplication.py --pair Docs/LED_SPEC.md led-node/README.md

Shingles rather than fixed phrases, because the duplication that matters is the
one nobody thought to grep for. At the default width it also surfaces shared
vocabulary, so it reports candidates and a human decides: Rule 25's test is
whether the passage is *arguing* or *warning*.
"""
import argparse
import ast
import io
import os
import re
import subprocess
import tokenize
from collections import defaultdict

ROOTS = ["Docs", "backend", "frontend/src", "led-node/main", "led-node/components"]
EXTRA = ["README.md", "led-node/README.md"]
SKIP_DIR = {"venv", "node_modules", "__pycache__", "dist", "build", "tests",
            "managed_components", ".git", "photos", "overlays", "host_test"}
SKIP_FILE = {"conftest.py"}
EXT = (".md", ".py", ".jsx", ".js", ".c", ".h")


# ── gathering ────────────────────────────────────────────────────────────────

def keep(path):
    """Same exclusions for a git-diff list as for a directory walk. Tests are
    out: a test docstring restating the rule it pins is the point of it."""
    parts = path.split("/")
    return (path.endswith(EXT)
            and parts[-1] not in SKIP_FILE
            and not (set(parts[:-1]) & SKIP_DIR))


def walk():
    out = []
    for root in ROOTS:
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR]
            out += [os.path.join(dirpath, n).replace("\\", "/")
                    for n in names
                    if n.endswith(EXT) and n not in SKIP_FILE]
    return sorted(set(out + [p for p in EXTRA if os.path.exists(p)]))


def _with_docs(listing):
    """A file list, plus all of Docs/ — a code change usually duplicates a
    document it did not itself edit."""
    files = {f for f in listing.splitlines() if f.strip() and keep(f)}
    files |= {p for p in walk() if p.startswith("Docs/")}
    return sorted(f for f in files if os.path.exists(f))


def changed_since(ref):
    """Files a branch has committed."""
    return _with_docs(subprocess.run(
        ["git", "diff", "--name-only", ref + "...HEAD"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace").stdout)


def changed_staged():
    """Files staged for the commit being made — the pre-commit hook's view.

    Distinct from changed_since, which lists what a branch has already
    committed and therefore cannot see the change you are about to make. A hook
    running the wrong one reports the branch as clean while staging duplication.
    """
    return _with_docs(subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace").stdout)


def read(path):
    return io.open(path, encoding="utf-8", errors="ignore").read()


def read_at(ref, path):
    # Explicit encoding: git output is UTF-8, and the default on Windows is not.
    r = subprocess.run(["git", "show", ref + ":" + path],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


# ── prose extraction ─────────────────────────────────────────────────────────

def py_prose(src):
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append(tok.string.lstrip("#"))
    except Exception:
        pass
    try:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                if doc:
                    out.append(doc)
    except Exception:
        pass
    return "\n".join(out)


def c_prose(src):
    return "\n".join(re.findall(r"/\*(.*?)\*/", src, re.S)
                     + re.findall(r"(?<![:\w])//(.*)$", src, re.M))


def md_prose(src):
    src = re.sub(r"```.*?```", " ", src, flags=re.S)   # fenced code
    return re.sub(r"^\s*\|.*$", " ", src, flags=re.M)  # tables


def prose(path, src):
    if path.endswith(".py"):
        return py_prose(src)
    if path.endswith((".jsx", ".js", ".c", ".h")):
        return c_prose(src)
    return md_prose(src)


def normalise(text):
    text = re.sub(r"`[^`]*`", " ", text)                  # inline code
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # markdown links
    text = text.replace("—", " ").replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


# ── comparison ───────────────────────────────────────────────────────────────

def shingles(text, width):
    w = normalise(text).split()
    return {" ".join(w[i:i + width]) for i in range(len(w) - width + 1)}


def pairs_for(files, getter, width):
    index = defaultdict(set)
    for p in files:
        for sh in shingles(prose(p, getter(p)), width):
            index[sh].add(p)
    counts = defaultdict(int)
    for sh, owners in index.items():
        if len(owners) > 1:
            counts[tuple(sorted(owners))] += 1
    return counts


def runs(a, b, width):
    """Contiguous shared word runs between two files, longest first."""
    wa = normalise(prose(a, read(a))).split()
    wbl = normalise(prose(b, read(b))).split()
    wb = {" ".join(wbl[i:i + width]) for i in range(len(wbl) - width + 1)}
    found, i = [], 0
    while i < len(wa) - width + 1:
        if " ".join(wa[i:i + width]) in wb:
            j = i + width
            while j < len(wa) and " ".join(wa[j - width + 1:j + 1]) in wb:
                j += 1
            found.append(" ".join(wa[i:j]))
            i = j
        else:
            i += 1
    return sorted(found, key=len, reverse=True)


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="REF",
                    help="only files changed against REF, with the same scan at "
                         "REF subtracted so pre-existing overlap is not reported")
    ap.add_argument("--pair", nargs=2, metavar=("A", "B"),
                    help="print the shared passages between two files")
    ap.add_argument("--min", type=int, default=1, metavar="N",
                    help="only report pairs sharing at least N shingles")
    ap.add_argument("--width", type=int, default=7, metavar="W",
                    help="shingle length in words (default 7)")
    ap.add_argument("--staged", action="store_true",
                    help="scan what is staged for commit rather than what the "
                         "branch has committed (for the pre-commit hook)")
    ap.add_argument("--fail", action="store_true",
                    help="exit 1 if anything is reported")
    args = ap.parse_args()

    if args.pair:
        a, b = args.pair
        for r in runs(a, b, args.width):
            print("  * " + r)
        return 0

    if args.staged:
        files = changed_staged()
    elif args.since:
        files = changed_since(args.since)
    else:
        files = walk()
    counts = pairs_for(files, read, args.width)

    if args.since:
        # Same files as they were at REF: whatever already overlapped there is
        # not this branch's problem.
        before = pairs_for(files, lambda p: read_at(args.since, p), args.width)
        counts = {k: v for k, v in counts.items() if k not in before}

    reported = sorted(((k, v) for k, v in counts.items() if v >= args.min),
                      key=lambda kv: -kv[1])
    print("scanned %d files, shingle=%d, %d pair(s) reported\n"
          % (len(files), args.width, len(reported)))

    for group, count in reported:
        print("[%3d] %s" % (count, "  <->  ".join(group)))
        if len(group) == 2:
            for r in runs(group[0], group[1], args.width)[:3]:
                print("       * " + r[:150])
        print()

    if reported:
        print("Rule 25: a passage in two files means one of them should be a "
              "link. Warnings stay, arguments move.")
    return 1 if (reported and args.fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
