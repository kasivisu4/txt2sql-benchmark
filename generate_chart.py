"""Standalone script to generate the QAS sensitivity chart.

Runs the benchmark against LM Studio + sakila.db, sweeps w from 0.0 to 1.0,
and saves a matplotlib line chart to assets/qas_analysis_example.png.

Usage:
    python generate_chart.py
    python generate_chart.py --input data/sakila_test_cases.json --output assets/qas_analysis_example.png
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # non-interactive backend — no GUI window needed
import matplotlib.pyplot as plt
from openai import OpenAI

from config import (
    DEFAULT_QAS_WEIGHT,
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
        weight=DEFAULT_QAS_WEIGHT,
        table_order_sensitive=TABLE_SIM_ORDER_SENSITIVE,
        table_order_mismatch_weight=TABLE_SIM_ORDER_MISMATCH_WEIGHT,
    )

    weights = [w / 10 for w in range(11)]  # 0.0, 0.1, ..., 1.0

    # Compute QAS for each query at each weight level
    qas_by_query = []
    for result in results:
        qas_values = []
        for w in weights:
            qas = (
                (1 - w) * result.semantic_sim
                + w * result.table_sim
                - result.missing_column_penalty
            )
            qas_values.append(max(0.0, min(1.0, qas)))
        qas_by_query.append(qas_values)

    # Average QAS across all queries at each weight
    avg_qas = [
        sum(qas_by_query[q][wi] for q in range(len(results))) / len(results)
        for wi in range(len(weights))
    ]

    # ── Plot ──────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.set_title("QAS vs Weight (w) for Sakila Benchmark Queries", fontsize=13)

    # Per-query lines
    for i, (result, qas_values) in enumerate(zip(results, qas_by_query)):
        ax.plot(weights, qas_values, marker="o", label=f"Q{i + 1}")

    # Average QAS as a dashed black line
    ax.plot(weights, avg_qas, linestyle="--", color="black", linewidth=2, label="Average QAS")

    ax.set_xlabel(
        "Weight (w)\nLow w = more semantic focus | High w = more result-set focus",
        fontsize=10,
    )
    ax.set_ylabel("QAS")
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.05)
    ax.legend(fontsize=9, loc="lower right", ncol=2)
    ax.grid(True, linestyle="--", alpha=0.5)

    fig.tight_layout()

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_file, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Chart saved to: {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate QAS sensitivity chart")
    parser.add_argument(
        "--input",
        default="data/sakila_test_cases.json",
        help="Path to test cases JSON file",
    )
    parser.add_argument(
        "--output",
        default="assets/qas_analysis_example.png",
        help="Output PNG file path",
    )
    args = parser.parse_args()
    generate_chart(args.input, args.output)
