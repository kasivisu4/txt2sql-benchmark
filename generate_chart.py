"""Standalone script to generate the composite score component chart.

Runs the benchmark against LM Studio + sakila.db and saves a matplotlib bar
chart showing per-query component breakdown to assets/composite_analysis.png.

Usage:
    python generate_chart.py
    python generate_chart.py --input data/sakila_test_cases.json --output assets/composite_analysis.png
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # non-interactive backend — no GUI window needed
import matplotlib.pyplot as plt
import numpy as np
from openai import OpenAI

from config import (
    WEIGHT_TABLE_SIM,
    WEIGHT_SEMANTIC_SIM,
    WEIGHT_LLM_SCORE,
    WEIGHT_VES,
    LM_STUDIO_API_URL,
    SQLITE_DB_PATH,
    TABLE_SIM_ORDER_SENSITIVE,
    TABLE_SIM_ORDER_MISMATCH_WEIGHT,
)
from mock_database import MockDatabaseExecutor
from metric import run_benchmark
from model import TestCase


def load_test_cases(json_file: str) -> list[TestCase]:
    with open(json_file, "r") as f:
        data = json.load(f)
    return [
        TestCase(
            natural_language=item["natural_language"],
            generated_sql=item["generated_sql"],
            expected_sql=item["expected_sql"],
        )
        for item in data
    ]


def generate_chart(input_file: str, output_file: str) -> None:
    print(f"Loading test cases from: {input_file}")
    test_cases = load_test_cases(input_file)

    db = MockDatabaseExecutor(SQLITE_DB_PATH)
    client = OpenAI(base_url=LM_STUDIO_API_URL, api_key="lm-studio")

    print(f"Running benchmark ({len(test_cases)} queries)...")
    results, _ = run_benchmark(
        test_cases,
        db,
        client,
        w1=WEIGHT_TABLE_SIM,
        w2=WEIGHT_SEMANTIC_SIM,
        w3=WEIGHT_LLM_SCORE,
        w4=WEIGHT_VES,
        table_order_sensitive=TABLE_SIM_ORDER_SENSITIVE,
        table_order_mismatch_weight=TABLE_SIM_ORDER_MISMATCH_WEIGHT,
    )

    # ── Plot: stacked bar chart of weighted components ────────────────────────
    labels = [f"Q{i + 1}" for i in range(len(results))]
    st_vals = [r.table_sim * WEIGHT_TABLE_SIM for r in results]
    sc_vals = [r.semantic_sim * WEIGHT_SEMANTIC_SIM for r in results]
    llm_vals = [r.llm_score * WEIGHT_LLM_SCORE for r in results]
    ves_vals = [r.ves * WEIGHT_VES for r in results]

    x = np.arange(len(labels))
    width = 0.6

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.set_title("Composite Score Breakdown by Query", fontsize=13)

    bars_st = ax.bar(x, st_vals, width, label=f"S_T (W1={WEIGHT_TABLE_SIM})")
    bars_sc = ax.bar(
        x, sc_vals, width, bottom=st_vals, label=f"S_C (W2={WEIGHT_SEMANTIC_SIM})"
    )
    bottom2 = [a + b for a, b in zip(st_vals, sc_vals)]
    bars_llm = ax.bar(
        x, llm_vals, width, bottom=bottom2, label=f"LLM (W3={WEIGHT_LLM_SCORE})"
    )
    bottom3 = [a + b for a, b in zip(bottom2, llm_vals)]
    bars_ves = ax.bar(
        x, ves_vals, width, bottom=bottom3, label=f"VES (W4={WEIGHT_VES})"
    )

    # Composite score markers
    composites = [r.composite_score for r in results]
    ax.plot(x, composites, "ko", markersize=5, label="Composite")

    ax.set_xlabel("Query", fontsize=10)
    ax.set_ylabel("Score Contribution")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0.0, 1.05)
    ax.legend(fontsize=9, loc="lower right")
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)

    fig.tight_layout()

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_file, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Chart saved to: {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate composite score chart")
    parser.add_argument(
        "--input",
        default="data/sakila_test_cases.json",
        help="Path to test cases JSON file",
    )
    parser.add_argument(
        "--output",
        default="assets/composite_analysis.png",
        help="Output PNG file path",
    )
    args = parser.parse_args()
    generate_chart(args.input, args.output)
