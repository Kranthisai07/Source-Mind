"""Evaluation report generator — renders results.json as a Markdown table.

Usage:
    python -m evaluation.report \
        --results evaluation/data/results.json \
        --output evaluation/data/report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _fmt(value: Any, decimals: int = 3) -> str:
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def generate_report(results_path: str, output_path: str | None = None) -> str:
    """Load results.json and generate a Markdown report.

    Args:
        results_path: Path to the evaluation results JSON file.
        output_path: If given, write the Markdown to this path.

    Returns:
        The generated Markdown string.
    """
    with Path(results_path).open(encoding="utf-8") as f:
        data = json.load(f)

    retrievers = data.get("retrievers", [])
    results = data.get("results", {})
    generated_at = data.get("generated_at", "unknown")

    lines = [
        "# SourceMind Evaluation Report",
        "",
        f"Generated: {generated_at}  ",
        f"Ground-truth items: {data.get('ground_truth_items', '?')}",
        "",
        "## Summary",
        "",
    ]

    # ── Summary table ─────────────────────────────────────────────────────────
    col_width = max(len(r) for r in retrievers) + 2 if retrievers else 10
    metric_cols = [
        ("Knowledge Retention (recall@5)", "knowledge_retention", "score"),
        ("Role Scope (engineer)", "role_scoped_retrieval_engineer", "score"),
        ("Role Scope (manager)", "role_scoped_retrieval_manager", "score"),
        ("Latency p95 (ms)", "latency", "p95_ms"),
        # conflict_detection is deliberately absent: rendering it as an
        # "n/a" cell reads as a hole in the results. It gets its own
        # section below, stating why.
    ]

    headers = ["Retriever"] + [mc[0] for mc in metric_cols]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["-" * (len(h) + 2) for h in headers]) + "|")

    for retriever in retrievers:
        r_results = results.get(retriever, {})
        row = [retriever]
        for _, metric_key, field in metric_cols:
            metric_result = r_results.get(metric_key, {})
            value = metric_result.get(field, "n/a")
            row.append(_fmt(value))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("## Excluded Metrics")
    lines.append("")
    excluded = data.get("excluded_metrics", {})
    if excluded:
        for metric_name, reason in excluded.items():
            lines.append(f"**{metric_name}** — excluded by design, not missing.")
            lines.append("")
            lines.append(f"> {reason}")
            lines.append("")
    else:
        lines.append("None.")
        lines.append("")
    lines.append("## Detailed Results")
    lines.append("")

    for retriever in retrievers:
        lines.append(f"### {retriever}")
        lines.append("")
        r_results = results.get(retriever, {})
        for metric_key, metric_data in r_results.items():
            if metric_data.get("status") == "excluded":
                continue
            lines.append(f"**{metric_key}**")
            lines.append("")
            lines.append("| Field | Value |")
            lines.append("|-------|-------|")
            for k, v in metric_data.items():
                lines.append(f"| {k} | {_fmt(v)} |")
            lines.append("")

    report = "\n".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report, encoding="utf-8")
        print(f"Report written to {output_path}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate evaluation Markdown report")
    parser.add_argument("--results", default="evaluation/data/results.json")
    parser.add_argument("--output", default="evaluation/data/report.md")
    args = parser.parse_args()

    if not Path(args.results).exists():
        print(f"ERROR: {args.results} not found", file=sys.stderr)
        sys.exit(1)

    report = generate_report(args.results, args.output)
    print(report)


if __name__ == "__main__":
    main()
