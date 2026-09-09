#!/usr/bin/env bash

OUTPUT_FILE="HANDOFF_SUMMARY.md"

echo "# AI Handoff Summary" > "$OUTPUT_FILE"
echo "Generated at: $(date)" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo "## 1. Recent Git Commits" >> "$OUTPUT_FILE"
echo "\`\`\`" >> "$OUTPUT_FILE"
git log -n 5 --oneline >> "$OUTPUT_FILE"
echo "\`\`\`" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo "## 2. Git Status (Uncommitted/Modified Files)" >> "$OUTPUT_FILE"
echo "\`\`\`" >> "$OUTPUT_FILE"
git status --short >> "$OUTPUT_FILE"
echo "\`\`\`" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo "## 3. Active Project Context" >> "$OUTPUT_FILE"
cat PROJECT_CONTEXT.md >> "$OUTPUT_FILE"
echo "Handoff summary generated in $OUTPUT_FILE"
