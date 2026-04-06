"""Main entry point for txt2sql benchmark suite.

Loads test cases, runs metrics, and exports Excel report.

Usage:
    python main.py --input data/test_cases.json --output results/report.xlsx --weight 0.3
"""

import json
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import List

from openai import OpenAI
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from model import TestCase, MetricResult
from config import (
    DEFAULT_QAS_WEIGHT,
    LM_STUDIO_API_URL,
    EMBEDDING_MODEL,
    MISSING_COLUMN_PENALTY_WEIGHT,
    SQLITE_DB_PATH,
    TABLE_SIM_ORDER_SENSITIVE,
    TABLE_SIM_ORDER_MISMATCH_WEIGHT,
    COLUMN_SELECTION_LLM_ENABLED,
    COLUMN_SELECTION_MODEL,
)
from mock_database import MockDatabaseExecutor
from metric import run_benchmark


def load_test_cases(json_file: str) -> List[TestCase]:
    """Load test cases from JSON file.

    Expected format:
    [
        {
            "natural_language": "...",
            "generated_sql": "...",
            "expected_sql": "..."
        },
        ...
    ]
    """
    with open(json_file, "r") as f:
        data = json.load(f)

    test_cases = []
    for item in data:
        test_cases.append(
            TestCase(
                natural_language=item.get("natural_language", ""),
                generated_sql=item.get("generated_sql", ""),
                expected_sql=item.get("expected_sql", ""),
            )
        )

    return test_cases


def export_to_excel(
    results: List[MetricResult],
    summary_stats: dict,
    output_file: str,
) -> None:
    """Export benchmark results to Excel with multiple sheets.

    Creates sheets:
    1. Summary: Overall statistics
    2. Results: Per-query metrics
    3. Info: Configuration and instructions
    """
    wb = Workbook()
    wb.remove(wb.active)  # Remove default sheet

    # Sheet 1: Summary Statistics
    ws_summary = wb.create_sheet("Summary")
    _populate_summary_sheet(ws_summary, summary_stats, results)

    # Sheet 2: Per-Query Results
    ws_results = wb.create_sheet("Results")
    _populate_results_sheet(ws_results, results)

    # Sheet 3: Info
    ws_info = wb.create_sheet("Info")
    _populate_info_sheet(ws_info, summary_stats)

    # Sheet 4: QAS sensitivity analysis
    ws_analysis = wb.create_sheet("QAS Analysis")
    _populate_qas_analysis_sheet(ws_analysis, results)

    # Save workbook
    wb.save(output_file)
    print(f"\n✓ Excel report saved to: {output_file}")


def _clamp_score(value: float) -> float:
    """Clamp score to [0, 1]."""
    return max(0.0, min(1.0, value))


def _populate_qas_analysis_sheet(ws, results: List[MetricResult]) -> None:
    """Populate a weight sweep sheet and add charts for QAS sensitivity analysis."""
    ws["A1"] = "QAS Sensitivity Analysis"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A3"] = (
        "Use this sheet to see how each query's QAS changes as w moves from 0.0 to 1.0."
    )
    ws["A4"] = (
        "Higher w gives more weight to table similarity. Lower w gives more weight to semantic similarity."
    )
    ws["A5"] = (
        "Interpretation: low w focuses on SQL semantic similarity; high w focuses on result-set correctness."
    )

    weights = [index / 10 for index in range(11)]
    headers = ["Weight (w)"]
    headers.extend([f"Q{index + 1}" for index in range(len(results))])
    headers.append("Average QAS")

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=6, column=col_idx)
        cell.value = header
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(
            start_color="4472C4", end_color="4472C4", fill_type="solid"
        )
        cell.alignment = Alignment(horizontal="center")

    for row_offset, weight in enumerate(weights, start=7):
        ws.cell(row=row_offset, column=1).value = weight
        qas_values = []
        for result_index, result in enumerate(results, start=2):
            qas_value = _clamp_score(
                (1 - weight) * result.semantic_sim
                + weight * result.table_sim
                - result.missing_column_penalty
            )
            ws.cell(row=row_offset, column=result_index).value = qas_value
            qas_values.append(qas_value)

        avg_qas = sum(qas_values) / len(qas_values) if qas_values else 0.0
        ws.cell(row=row_offset, column=len(headers)).value = avg_qas

    ws["N1"] = "Query Legend"
    ws["N1"].font = Font(bold=True, size=12)
    ws["N2"] = "Series"
    ws["O2"] = "Natural Language"
    for cell_ref in ["N2", "O2"]:
        ws[cell_ref].font = Font(bold=True, color="FFFFFF")
        ws[cell_ref].fill = PatternFill(
            start_color="4472C4", end_color="4472C4", fill_type="solid"
        )

    for index, result in enumerate(results, start=1):
        ws.cell(row=index + 2, column=14).value = f"Q{index}"
        ws.cell(row=index + 2, column=15).value = result.test_case.natural_language

    ws["N10"] = "How to Read This"
    ws["N10"].font = Font(bold=True, size=12)
    ws["N11"] = "w close to 0.0"
    ws["O11"] = "More focus on SQL semantic similarity (S_C)."
    ws["N12"] = "w close to 1.0"
    ws["O12"] = "More focus on result-set/table similarity (S_T)."
    ws["N13"] = "Flat line"
    ws["O13"] = "Query score is not very sensitive to the weight choice."
    ws["N14"] = "Steep line"
    ws["O14"] = (
        "Query score depends strongly on whether you prioritize semantics or execution output."
    )

    chart = LineChart()
    chart.title = "QAS vs Weight by Query"
    chart.style = 2
    chart.y_axis.title = "QAS"
    chart.x_axis.title = "Weight (w)"
    chart.height = 10
    chart.width = 20

    data = Reference(ws, min_col=2, max_col=len(headers), min_row=6, max_row=17)
    cats = Reference(ws, min_col=1, min_row=7, max_row=17)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.legend.position = "r"
    ws.add_chart(chart, "A20")

    avg_chart = LineChart()
    avg_chart.title = "Average QAS vs Weight"
    avg_chart.style = 10
    avg_chart.y_axis.title = "Average QAS"
    avg_chart.x_axis.title = "Weight (w)"
    avg_chart.height = 8
    avg_chart.width = 12

    avg_data = Reference(
        ws,
        min_col=len(headers),
        max_col=len(headers),
        min_row=6,
        max_row=17,
    )
    avg_chart.add_data(avg_data, titles_from_data=True)
    avg_chart.set_categories(cats)
    ws.add_chart(avg_chart, "V20")

    ws.freeze_panes = "A7"
    ws.column_dimensions["A"].width = 12
    for col_idx in range(2, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 12
    ws.column_dimensions["N"].width = 10
    ws.column_dimensions["O"].width = 40


def _populate_summary_sheet(
    ws, summary_stats: dict, results: List[MetricResult]
) -> None:
    """Populate summary statistics sheet."""
    # Header
    ws["A1"] = "Benchmark Summary"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].fill = PatternFill(
        start_color="366092", end_color="366092", fill_type="solid"
    )
    ws["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    ws.merge_cells("A1:B1")

    # Metadata
    ws["A3"] = "Report Generated"
    ws["B3"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ws["A4"] = "Total Tests"
    ws["B4"] = int(summary_stats["total_tests"])

    ws["A5"] = "QAS Weight (w)"
    ws["B5"] = f"{summary_stats['weight']:.2f}"

    # Metrics
    ws["A7"] = "Metric"
    ws["B7"] = "Value"
    for cell in ["A7", "B7"]:
        ws[cell].font = Font(bold=True, color="FFFFFF")
        ws[cell].fill = PatternFill(
            start_color="4472C4", end_color="4472C4", fill_type="solid"
        )

    row = 8
    metrics = [
        ("EM Pass Rate", f"{summary_stats['em_pass_rate']*100:.1f}%"),
        ("EX Pass Rate", f"{summary_stats['ex_pass_rate']*100:.1f}%"),
        ("Avg Semantic Similarity", f"{summary_stats['avg_semantic_sim']:.4f}"),
        ("Avg Table Similarity", f"{summary_stats['avg_table_sim']:.4f}"),
        (
            "Avg Missing Column Penalty",
            f"{summary_stats['avg_missing_column_penalty']:.4f}",
        ),
        ("Avg QAS", f"{summary_stats['avg_qas']:.4f}"),
        ("Total Time (ms)", f"{summary_stats['total_time_ms']:.1f}"),
    ]

    for metric_name, metric_value in metrics:
        ws[f"A{row}"] = metric_name
        ws[f"B{row}"] = metric_value
        row += 1

    # Column widths
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 20


def _populate_results_sheet(ws, results: List[MetricResult]) -> None:
    """Populate per-query results sheet."""
    # Header
    headers = [
        "Query #",
        "Natural Language",
        "Generated SQL",
        "Expected SQL",
        "EM",
        "EX",
        "Semantic Sim",
        "Table Sim",
        "QAS",
        "Eval Gen Columns",
        "Eval Exp Columns",
        "Missing Expected Columns",
        "Missing Column Penalty",
        "Column Judge Source",
        "Column Judge Confidence",
        "Time (ms)",
    ]

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.value = header
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(
            start_color="4472C4", end_color="4472C4", fill_type="solid"
        )
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )

    # Data rows
    for row_idx, result in enumerate(results, start=2):
        ws.cell(row=row_idx, column=1).value = row_idx - 1
        ws.cell(row=row_idx, column=2).value = result.test_case.natural_language
        ws.cell(row=row_idx, column=3).value = result.test_case.generated_sql
        ws.cell(row=row_idx, column=4).value = result.test_case.expected_sql
        ws.cell(row=row_idx, column=5).value = "✓" if result.em else "✗"
        ws.cell(row=row_idx, column=6).value = "✓" if result.ex else "✗"
        ws.cell(row=row_idx, column=7).value = f"{result.semantic_sim:.4f}"
        ws.cell(row=row_idx, column=8).value = f"{result.table_sim:.4f}"
        ws.cell(row=row_idx, column=9).value = f"{result.qas:.4f}"
        ws.cell(row=row_idx, column=10).value = ", ".join(
            result.selected_generated_columns
        )
        ws.cell(row=row_idx, column=11).value = ", ".join(
            result.selected_expected_columns
        )
        ws.cell(row=row_idx, column=12).value = ", ".join(
            result.missing_expected_columns
        )
        ws.cell(row=row_idx, column=13).value = f"{result.missing_column_penalty:.4f}"
        ws.cell(row=row_idx, column=14).value = result.column_selection_source
        ws.cell(row=row_idx, column=15).value = (
            f"{result.column_selection_confidence:.2f}"
        )
        ws.cell(row=row_idx, column=16).value = f"{result.execution_time_ms:.1f}"

        # Center align numeric columns
        for col in [1, 5, 6, 7, 8, 9, 13, 15, 16]:
            ws.cell(row=row_idx, column=col).alignment = Alignment(horizontal="center")

    # Column widths
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 40
    ws.column_dimensions["D"].width = 40
    ws.column_dimensions["E"].width = 8
    ws.column_dimensions["F"].width = 8
    ws.column_dimensions["G"].width = 15
    ws.column_dimensions["H"].width = 15
    ws.column_dimensions["I"].width = 12
    ws.column_dimensions["J"].width = 24
    ws.column_dimensions["K"].width = 24
    ws.column_dimensions["L"].width = 24
    ws.column_dimensions["M"].width = 18
    ws.column_dimensions["N"].width = 18
    ws.column_dimensions["O"].width = 18
    ws.column_dimensions["P"].width = 12


def _populate_info_sheet(ws, summary_stats: dict) -> None:
    """Populate information sheet."""
    ws["A1"] = "Configuration & Instructions"
    ws["A1"].font = Font(bold=True, size=12)

    ws["A3"] = "Current Settings"
    ws["A3"].font = Font(bold=True, size=11)

    ws["A4"] = "QAS Weight (w)"
    ws["B4"] = f"{summary_stats['weight']:.2f}"

    ws["A5"] = "Formula"
    ws["B5"] = "QAS = (1-w)*SemanticSim + w*TableSim - MissingColumnPenalty"

    ws["A6"] = "Intent-Aware Columns"
    ws["B6"] = f"enabled={COLUMN_SELECTION_LLM_ENABLED}, model={COLUMN_SELECTION_MODEL}"

    ws["A7"] = "Missing Column Penalty Weight"
    ws["B7"] = f"{MISSING_COLUMN_PENALTY_WEIGHT:.2f}"

    ws["A9"] = "Instructions for Different Weights"
    ws["A9"].font = Font(bold=True, size=11)

    instructions = [
        "To test with a different weight (w), re-run the benchmark with the --weight parameter:",
        "",
        "Examples:",
        "  python main.py --weight 0.5    # 50% semantic, 50% table",
        "  python main.py --weight 0.1    # 90% semantic, 10% table",
        "  python main.py --weight 0.7    # 30% semantic, 70% table",
        "",
        (
            f"The default weight is {DEFAULT_QAS_WEIGHT:.1f} "
            f"({1 - DEFAULT_QAS_WEIGHT:.0%} semantic, {DEFAULT_QAS_WEIGHT:.0%} table similarity)."
        ),
        "Low w means the benchmark cares more about SQL semantic similarity.",
        "High w means the benchmark cares more about result-set correctness.",
    ]

    row = 10
    for instruction in instructions:
        ws[f"A{row}"] = instruction
        row += 1

    ws["A21"] = "Metric Definitions"
    ws["A21"].font = Font(bold=True, size=11)

    definitions = [
        ("EM (Exact Match)", "Binary: 1 if normalized SQL strings match, 0 otherwise"),
        ("EX (Execution Accuracy)", "Binary: 1 if query results match, 0 otherwise"),
        ("Semantic Similarity", "Cosine similarity of SQL embeddings [0,1]"),
        ("Table Similarity", "Edit distance-based column similarity [0,1]"),
        (
            "QAS (Query Affinity Score)",
            "Weighted combination of semantic and table similarity",
        ),
    ]

    row = 22
    for name, definition in definitions:
        ws[f"A{row}"] = name
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"B{row}"] = definition
        row += 1

    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 60


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="txt2sql Benchmark Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
    python main.py --input data/sakila_test_cases.json --output results/report.xlsx
  python main.py --weight 0.5
        """,
    )

    parser.add_argument(
        "--input",
        type=str,
        default="data/sakila_test_cases.json",
        help="Input JSON file with test cases (default: data/sakila_test_cases.json)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="results/benchmark_report.xlsx",
        help="Output Excel file for report (default: results/benchmark_report.xlsx)",
    )
    parser.add_argument(
        "--weight",
        type=float,
        default=DEFAULT_QAS_WEIGHT,
        help=f"QAS weight parameter (default: {DEFAULT_QAS_WEIGHT}). Formula: QAS = (1-w)*SemanticSim + w*TableSim",
    )
    parser.add_argument(
        "--table-order-sensitive",
        action="store_true",
        default=TABLE_SIM_ORDER_SENSITIVE,
        help=("Use order-sensitive table similarity (default is order-insensitive)."),
    )
    parser.add_argument(
        "--table-order-mismatch-weight",
        type=float,
        default=TABLE_SIM_ORDER_MISMATCH_WEIGHT,
        help=(
            "Soft penalty weight for shuffled row order in order-insensitive "
            "mode (default: 0.0)."
        ),
    )

    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("txt2sql Benchmark Suite")
    print("=" * 70)

    # Check input file exists
    if not Path(args.input).exists():
        print(f"❌ Error: Input file not found: {args.input}")
        return

    # Create output directory
    output_dir = Path(args.output).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load test cases
    print(f"\n📂 Loading test cases from: {args.input}")
    test_cases = load_test_cases(args.input)
    print(f"   ✓ Loaded {len(test_cases)} test case(s)")

    # Initialize services
    print(f"\n🔌 Initializing services...")

    # Initialize OpenAI client pointing to LM Studio
    try:
        openai_client = OpenAI(api_key="dummy", base_url=LM_STUDIO_API_URL)
        print(f"   ✓ Connected to LM Studio at {LM_STUDIO_API_URL}")
        print(f"   ✓ Embedding model: {EMBEDDING_MODEL}")
        if COLUMN_SELECTION_LLM_ENABLED:
            print(f"   ✓ Column selection model: {COLUMN_SELECTION_MODEL}")
    except Exception as e:
        print(f"   ⚠️  Warning: Could not connect to LM Studio: {e}")
        print(f"      Make sure LM Studio is running at {LM_STUDIO_API_URL}")
        print(
            f"      Continuing with mock embeddings (all similarities will be random)..."
        )
        openai_client = None

    # Initialize SQLite executor (sakila.db)
    db_executor = MockDatabaseExecutor(SQLITE_DB_PATH)
    print(f"   ✓ SQLite database initialized: {SQLITE_DB_PATH}")

    # Run benchmark
    print(f"\n📊 Running benchmark with weight={args.weight:.2f}...")
    print(f"   (QAS = (1-{args.weight:.2f})*SemanticSim + {args.weight:.2f}*TableSim)")
    print(
        "   (Table similarity mode: "
        + ("order-sensitive" if args.table_order_sensitive else "order-insensitive")
        + ")"
    )
    print(f"   (Order mismatch weight: {args.table_order_mismatch_weight:.2f})")
    print()

    results, summary_stats = run_benchmark(
        test_cases,
        db_executor,
        openai_client,
        weight=args.weight,
        table_order_sensitive=args.table_order_sensitive,
        table_order_mismatch_weight=args.table_order_mismatch_weight,
    )

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total Tests:        {int(summary_stats['total_tests'])}")
    print(f"EM Pass Rate:       {summary_stats['em_pass_rate']*100:.1f}%")
    print(f"EX Pass Rate:       {summary_stats['ex_pass_rate']*100:.1f}%")
    print(f"Avg Semantic Sim:   {summary_stats['avg_semantic_sim']:.4f}")
    print(f"Avg Table Sim:      {summary_stats['avg_table_sim']:.4f}")
    print(f"Avg QAS:            {summary_stats['avg_qas']:.4f}")
    print(f"Total Time:         {summary_stats['total_time_ms']:.1f} ms")

    # Export to Excel
    print(f"\n📝 Exporting results to Excel...")
    export_to_excel(results, summary_stats, args.output)

    print("\n" + "=" * 70)
    print("✓ Benchmark completed successfully!")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Benchmark interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
