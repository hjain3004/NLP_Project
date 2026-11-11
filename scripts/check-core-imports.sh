#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Deliberately conservative: forbidden references in comments are rejected too.
check_tree() {
    local directory="$1" pattern="$2" failed=0 result file
    if [ ! -d "$directory" ]; then
        echo "Missing source directory: $directory" >&2
        return 1
    fi
    while IFS= read -r -d '' file; do
        if grep -nHE "$pattern" "$file"; then
            failed=1
        else
            result=$?
            if [ "$result" -ne 1 ]; then
                return "$result"
            fi
        fi
    done < <(find "$directory" -type f -print0)
    return "$failed"
}

imports='(^|[^[:alnum:]_])import[[:space:]]+((class|struct|enum|protocol|func|var|let|typealias)[[:space:]]+)?'
boundary='([^[:alnum:]_]|$)'
failed=0
if ! check_tree Sources/CashBooksCore "${imports}(FoundationModels|PDFKit|Vision|SwiftUI|SwiftData|Network|UIKit|AppKit)${boundary}|(^|[^[:alnum:]_])(URLSession|Double|Float|CGFloat|NumberFormatter)${boundary}|(^|[^[:alnum:]_])Locale[[:space:]]*\.[[:space:]]*current${boundary}"; then
    failed=1
fi
if ! check_tree Sources/CashBooksDocument "${imports}Network${boundary}|(^|[^[:alnum:]_])URLSession${boundary}"; then
    failed=1
fi
if [ "$failed" -ne 0 ]; then
    echo "Core/document boundary check failed." >&2
    exit 1
fi
echo "Core/document boundary check passed."
