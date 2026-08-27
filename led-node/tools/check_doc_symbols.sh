#!/usr/bin/env bash
#
# check_doc_symbols.sh — assert that C symbols named in the led-node docs still
# exist in the source.
#
# Deliberately shallow: it checks EXISTENCE, not signatures. It would not have
# caught deg_end -> deg_span (same name, changed meaning), but it would have
# caught canvas_point -> canvas_add_point, which is the drift that actually
# happened. Cheap, no false-positive tax, no dependencies beyond grep.
#
# Usage:  led-node/tools/check_doc_symbols.sh
# Exit:   0 clean, 1 if any documented symbol is missing.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
docs="$repo_root/Docs"
src="$repo_root/led-node"

# Where a symbol is allowed to resolve: public headers, the Kconfig menu, and
# the source filenames themselves (docs legitimately cite `transport_http.c`).
haystack=$(
    cat "$src"/components/*/include/*.h "$src"/main/Kconfig.projbuild 2>/dev/null
    find "$src" -name '*.c' -o -name '*.h' 2>/dev/null | xargs -n1 basename 2>/dev/null
)

if [ -z "$haystack" ]; then
    echo "check_doc_symbols: found no headers under $src — wrong path?" >&2
    exit 1
fi

# Symbol shapes we track. Anything matching these inside a `backtick span` is
# expected to resolve. Trailing-underscore matches are dropped below: they are
# glob stubs in prose ("LED_NODE_WIFI_* options"), not real symbols.
pattern='\b(canvas_[a-z_]+|rgbw_[a-z_]+|modes_[a-z_]+|anim_[a-z_]+|command_[a-z_]+|transport_[a-z_]+|output_[a-z_]+|MODE_[A-Z0-9_]+|CMD_[A-Z0-9_]+|CONFIG_LED_NODE_[A-Z0-9_]+|LED_NODE_[A-Z0-9_]+)\b'

findings=""

for doc in "$docs"/LED_NODE_*.md "$docs"/LED_SPEC.md; do
    [ -f "$doc" ] || continue

    # Inline `spans` AND fenced code blocks. Prose is excluded, so a word like
    # "output" or a mode name in English is not a false positive.
    #
    # Fenced blocks were missed by the first version of this script, which let a
    # stale canvas_point/canvas_arc reference survive inside the component
    # layout tree through the very commit that fixed it everywhere else.
    while IFS= read -r hit; do
        lineno="${hit%%:*}"
        span="${hit#*:}"

        while IFS= read -r sym; do
            [ -n "$sym" ] || continue
            case "$sym" in
                *_) continue ;;   # glob stub, e.g. LED_NODE_WIFI_*
            esac
            if ! printf '%s' "$haystack" | grep -qE "\b${sym}\b"; then
                findings+="${doc#"$repo_root"/}:${lineno}: undefined symbol in docs: ${sym}"$'\n'
            fi
        done < <(printf '%s' "$span" | grep -oE "$pattern" | sort -u)
    done < <(
        grep -on '`[^`]*`' "$doc" 2>/dev/null
        # Lines inside ``` fences, with their real line numbers.
        awk '/^```/ {inblock = !inblock; next} inblock {print NR ":" $0}' "$doc"
    )
done

if [ -n "$findings" ]; then
    printf '%s' "$findings" >&2
    echo "check_doc_symbols: drift found — the docs name symbols the source does not define" >&2
    exit 1
fi

echo "check_doc_symbols: clean"
