"""Metric calculators for txt2sql benchmark suite.

Computes:
- S_C (Semantic Similarity): Cosine similarity of code embeddings from LM Studio
- S_T (Table Similarity): Edit distance-based similarity of result tables
- LLM Score: LLM-as-judge evaluation of generated SQL quality
- VES (Valid Efficiency Score): Execution speed relative to reference query
- Composite Score: Weighted combination of S_T, S_C, LLM Score, and VES
"""

import json
import math
import re
from typing import List
import time
from model import (
    TestCase,
    MetricResult,
    QueryResult,
    normalize_sql,
    cosine_similarity,
    edit_distance,
)
from mock_database import MockDatabaseExecutor
from config import (
    EXECUTION_FAILURE_PENALTY,
    WEIGHT_TABLE_SIM,
    WEIGHT_SEMANTIC_SIM,
    WEIGHT_LLM_SCORE,
    WEIGHT_VES,
    EMBEDDING_MODEL,
    TABLE_SIM_ORDER_SENSITIVE,
    TABLE_SIM_ORDER_MISMATCH_WEIGHT,
    COLUMN_SELECTION_LLM_ENABLED,
    COLUMN_SELECTION_MODEL,
    COLUMN_SELECTION_TEMPERATURE,
    LLM_JUDGE_MODEL,
    LLM_JUDGE_TEMPERATURE,
)


def _position_mismatch_ratio(values_a: list[str], values_b: list[str]) -> float:
    """Return fraction of positions that differ between two value lists."""
    max_len = max(len(values_a), len(values_b))
    if max_len == 0:
        return 0.0

    mismatches = 0
    for idx in range(max_len):
        a = values_a[idx] if idx < len(values_a) else ""
        b = values_b[idx] if idx < len(values_b) else ""
        if a != b:
            mismatches += 1

    return mismatches / max_len


def _extract_json_object(raw_text: str) -> dict:
    """Extract a JSON object from an LLM response."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        raise ValueError("No JSON object found in LLM response")

    return json.loads(text[start_idx : end_idx + 1])


def _fallback_column_selection(
    result_gen: QueryResult,
    result_ref: QueryResult,
) -> dict:
    """Fallback strategy when LLM column selection is unavailable."""
    common_columns = [col for col in result_gen.columns if col in result_ref.columns]
    if common_columns:
        return {
            "generated_columns": common_columns,
            "expected_columns": common_columns,
            "column_mapping": {col: col for col in common_columns},
            "confidence": 0.5,
            "source": "common-columns",
        }

    return {
        "generated_columns": list(result_gen.columns),
        "expected_columns": list(result_ref.columns),
        "column_mapping": {},
        "confidence": 0.0,
        "source": "all-columns",
    }


def select_relevant_columns(
    natural_language: str,
    generated_sql: str,
    expected_sql: str,
    result_gen: QueryResult,
    result_ref: QueryResult,
    openai_client,
) -> dict:
    """Use an LLM to select the columns relevant to the user query.

    The returned structure contains the generated columns to evaluate,
    the corresponding expected columns, a mapping, confidence, and source.
    """
    fallback = _fallback_column_selection(result_gen, result_ref)

    if (
        not COLUMN_SELECTION_LLM_ENABLED
        or openai_client is None
        or not result_gen.succeeded
        or not result_ref.succeeded
    ):
        return fallback

    try:
        response = openai_client.chat.completions.create(
            model=COLUMN_SELECTION_MODEL,
            temperature=COLUMN_SELECTION_TEMPERATURE,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are evaluating text-to-SQL outputs. "
                        "Return JSON only. Select only the columns required to answer "
                        "the user query. Ignore extra generated columns unless they are "
                        "required by the question."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Select the result columns that should be used for evaluation.\n"
                        f"User query: {natural_language}\n"
                        f"Generated SQL: {generated_sql}\n"
                        f"Expected SQL: {expected_sql}\n"
                        f"Generated result columns: {result_gen.columns}\n"
                        f"Expected result columns: {result_ref.columns}\n\n"
                        "Return JSON with this exact schema:\n"
                        "{\n"
                        '  "generated_columns": ["..."],\n'
                        '  "expected_columns": ["..."],\n'
                        '  "column_mapping": {"generated_col": "expected_col"},\n'
                        '  "confidence": 0.0\n'
                        "}"
                    ),
                },
            ],
        )
        message = response.choices[0].message.content or ""
        payload = _extract_json_object(message)

        generated_columns = [
            col
            for col in payload.get("generated_columns", [])
            if col in result_gen.columns
        ]
        expected_columns = [
            col
            for col in payload.get("expected_columns", [])
            if col in result_ref.columns
        ]

        raw_mapping = payload.get("column_mapping", {})
        column_mapping = {}
        for gen_col, exp_col in raw_mapping.items():
            if gen_col in result_gen.columns and exp_col in result_ref.columns:
                column_mapping[gen_col] = exp_col

        for gen_col in generated_columns:
            if gen_col not in column_mapping and gen_col in expected_columns:
                column_mapping[gen_col] = gen_col

        generated_columns = [col for col in generated_columns if col in column_mapping]
        expected_columns = [column_mapping[col] for col in generated_columns]

        if not generated_columns:
            return fallback

        confidence = payload.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0

        return {
            "generated_columns": generated_columns,
            "expected_columns": expected_columns,
            "column_mapping": column_mapping,
            "confidence": max(0.0, min(1.0, confidence)),
            "source": "llm",
        }

    except Exception as e:
        print(f"Warning: Failed to select relevant columns with LLM: {e}")
        return fallback


def _project_result_for_comparison(
    result: QueryResult,
    column_mapping: dict[str, str],
) -> QueryResult:
    """Project a query result into canonical comparison columns."""
    if not column_mapping:
        return result

    projected_rows = []
    for row in result.rows:
        projected_row = {
            canonical_col: row.get(source_col, "")
            for canonical_col, source_col in column_mapping.items()
        }
        projected_rows.append(projected_row)

    return QueryResult(
        rows=projected_rows,
        columns=list(column_mapping.keys()),
        succeeded=result.succeeded,
        error_message=result.error_message,
    )


def _calculate_table_similarity_from_results(
    result_gen: QueryResult,
    result_ref: QueryResult,
    order_sensitive: bool = TABLE_SIM_ORDER_SENSITIVE,
    order_mismatch_weight: float = TABLE_SIM_ORDER_MISMATCH_WEIGHT,
) -> float:
    """Calculate table similarity directly from already-executed results."""
    if not result_gen.succeeded or not result_ref.succeeded:
        return 0.0

    if len(result_gen.rows) == 0 and len(result_ref.rows) == 0:
        return 1.0

    n_cols_gen = len(result_gen.columns)
    n_cols_ref = len(result_ref.columns)

    if n_cols_gen == 0 or n_cols_ref == 0:
        return 0.0

    max_rows = max(len(result_gen.rows), len(result_ref.rows))
    if max_rows == 0:
        max_rows = 1

    total_distance = 0.0

    for col_gen in result_gen.columns:
        min_col_distance = float("inf")
        gen_values_original = [str(row.get(col_gen, "")) for row in result_gen.rows]
        gen_values = gen_values_original
        if not order_sensitive:
            gen_values = sorted(gen_values_original)
        gen_string = "|".join(gen_values)

        for col_ref in result_ref.columns:
            ref_values_original = [str(row.get(col_ref, "")) for row in result_ref.rows]
            ref_values = ref_values_original
            if not order_sensitive:
                ref_values = sorted(ref_values_original)
            ref_string = "|".join(ref_values)

            distance = edit_distance(gen_string, ref_string)

            if not order_sensitive and order_mismatch_weight > 0:
                mismatch_ratio = _position_mismatch_ratio(
                    gen_values_original,
                    ref_values_original,
                )
                distance += mismatch_ratio * max_rows * order_mismatch_weight

            min_col_distance = min(min_col_distance, distance)

        normalized_distance = min_col_distance / max_rows
        total_distance += normalized_distance

    max_cols = max(n_cols_gen, n_cols_ref)
    table_sim = 1.0 - (total_distance / max_cols)
    return max(0.0, min(1.0, table_sim))


def calculate_ves(
    ex: bool,
    exec_time_gen_ms: float,
    exec_time_ref_ms: float,
) -> float:
    """Calculate Valid Efficiency Score (VES).

    Based on the BIRD benchmark metric. Measures how efficiently the
    generated SQL executes relative to the reference query.

    Formula: VES = 1(valid) * sqrt(ref_time / gen_time)
    - 1(valid) = 1 if EX is True (correct results), else 0
    - Capped at 1.0 to maintain [0, 1] range

    Args:
        ex: Whether the generated query produces correct results (EX metric)
        exec_time_gen_ms: Execution time of generated query in milliseconds
        exec_time_ref_ms: Execution time of reference query in milliseconds

    Returns:
        VES score in range [0, 1]
    """
    if not ex:
        return 0.0

    if exec_time_gen_ms <= 0:
        return 0.0

    if exec_time_ref_ms <= 0:
        return 1.0

    ves = math.sqrt(exec_time_ref_ms / exec_time_gen_ms)
    return min(1.0, ves)


def _format_query_result(result: QueryResult, max_rows: int = 5) -> str:
    """Format a QueryResult into a concise string for LLM consumption."""
    if not result.succeeded:
        return f"ERROR: {result.error_message}"
    if not result.rows:
        return f"Columns: {result.columns}\nRows: (empty)"
    preview = result.rows[:max_rows]
    lines = [f"Columns: {result.columns}"]
    for row in preview:
        lines.append(str(row))
    if len(result.rows) > max_rows:
        lines.append(f"... ({len(result.rows)} rows total)")
    return "\n".join(lines)


def calculate_llm_score(
    natural_language: str,
    generated_sql: str,
    expected_sql: str,
    result_gen: QueryResult,
    result_ref: QueryResult,
    openai_client,
) -> tuple[float, str]:
    """Calculate LLM-as-Judge score for generated SQL quality.

    Sends the natural language query, both SQL queries, and their execution
    results to an LLM which classifies the match as fully-matched (1.0),
    partial (0.5), or no match (0.0), along with a reasoning explanation.

    Args:
        natural_language: The original natural language query
        generated_sql: The generated SQL query to evaluate
        expected_sql: The reference (expected) SQL query
        result_gen: Execution result of the generated SQL
        result_ref: Execution result of the reference SQL
        openai_client: OpenAI-compatible client (or None)

    Returns:
        Tuple of (score, reasoning).
        Score: 1.0 (fully-matched), 0.5 (partial), or 0.0 (no match).
        Reasoning: LLM explanation for the classification.
    """
    if normalize_sql(generated_sql) == normalize_sql(expected_sql):
        return 1.0, "SQL queries are identical after normalization."

    if openai_client is None:
        return 0.5, "LLM unavailable — default score."

    gen_result_str = _format_query_result(result_gen)
    ref_result_str = _format_query_result(result_ref)

    try:
        response = openai_client.chat.completions.create(
            model=LLM_JUDGE_MODEL,
            temperature=LLM_JUDGE_TEMPERATURE,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert SQL evaluator. You will be given a natural "
                        "language question, a generated SQL query, a reference SQL "
                        "query, and the execution results of both queries. "
                        "Classify the generated SQL into exactly one category:\n\n"
                        "- fully-matched: The generated SQL correctly and completely "
                        "answers the question with equivalent logic to the reference.\n"
                        "- partial: The generated SQL partially answers the question "
                        "or captures some but not all aspects of the intent.\n"
                        "- no match: The generated SQL does not answer the question "
                        "or is fundamentally wrong.\n\n"
                        "Respond in exactly this format (two lines):\n"
                        "Classification: <fully-matched | partial | no match>\n"
                        "Reasoning: <one or two sentence explanation>"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Natural language question: {natural_language}\n\n"
                        f"Generated SQL: {generated_sql}\n"
                        f"Generated SQL result:\n{gen_result_str}\n\n"
                        f"Reference SQL: {expected_sql}\n"
                        f"Reference SQL result:\n{ref_result_str}\n\n"
                        "Evaluation:"
                    ),
                },
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        message = raw.lower()

        # Parse reasoning
        reasoning = ""
        for line in raw.split("\n"):
            if line.strip().lower().startswith("reasoning:"):
                reasoning = line.strip()[len("reasoning:") :].strip()
                break
        if not reasoning:
            reasoning = raw  # Fallback: use full response as reasoning

        if "fully-matched" in message or "fully matched" in message:
            return 1.0, reasoning
        elif "no match" in message or "no_match" in message:
            return 0.0, reasoning
        elif "partial" in message:
            return 0.5, reasoning
        else:
            print(f"Warning: Unexpected LLM judge response: {raw!r}, defaulting to 0.5")
            return 0.5, reasoning or "Unexpected response from LLM judge."

    except Exception as e:
        print(f"Warning: Failed to compute LLM judge score: {e}")
        return 0.5, f"LLM error: {e}"


def calculate_composite_score(
    table_sim: float,
    semantic_sim: float,
    llm_score: float,
    ves: float,
    w1: float = WEIGHT_TABLE_SIM,
    w2: float = WEIGHT_SEMANTIC_SIM,
    w3: float = WEIGHT_LLM_SCORE,
    w4: float = WEIGHT_VES,
) -> float:
    """Calculate Composite Score.

    Formula: Composite = (W1 * S_T) + (W2 * S_C) + (W3 * LLM_SCORE) + (W4 * VES)

    Args:
        table_sim: Table similarity score (S_T) [0, 1]
        semantic_sim: Semantic similarity score (S_C) [0, 1]
        llm_score: LLM-as-Judge score [0, 1]
        ves: Valid Efficiency Score [0, 1]
        w1: Weight for table similarity (default from config)
        w2: Weight for semantic similarity (default from config)
        w3: Weight for LLM score (default from config)
        w4: Weight for VES (default from config)

    Returns:
        Composite score in range [0, 1]
    """
    score = w1 * table_sim + w2 * semantic_sim + w3 * llm_score + w4 * ves
    return max(0.0, min(1.0, score))


def calculate_semantic_similarity(
    generated_sql: str,
    expected_sql: str,
    openai_client,
) -> float:
    """Calculate semantic similarity using code embeddings from LM Studio.

    Uses OpenAI-compatible API (LM Studio) to get embeddings,
    then computes cosine similarity.

    Args:
        generated_sql: Generated SQL query
        expected_sql: Expected SQL query
        openai_client: OpenAI-compatible client pointing to LM Studio (or None)

    Returns:
        Cosine similarity in range [0, 1]
    """
    # Optimization: if queries match exactly, return 1.0
    if normalize_sql(generated_sql) == normalize_sql(expected_sql):
        return 1.0

    # If no client available, return 0.5 (neutral value)
    if openai_client is None:
        return 0.5

    try:
        # Get embeddings from LM Studio via OpenAI-compatible API
        emb_gen_response = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=generated_sql,
        )
        emb_gen = emb_gen_response.data[0].embedding

        emb_ref_response = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=expected_sql,
        )
        emb_ref = emb_ref_response.data[0].embedding

        # Compute cosine similarity
        sim = cosine_similarity(emb_gen, emb_ref)
        return max(0.0, min(1.0, sim))  # Clamp to [0, 1]

    except Exception as e:
        print(f"Warning: Failed to compute semantic similarity: {e}")
        # Fallback: return 0.5 (neutral) on failure
        return 0.5


def run_benchmark(
    test_cases: List[TestCase],
    db_executor: MockDatabaseExecutor,
    openai_client,
    w1: float = WEIGHT_TABLE_SIM,
    w2: float = WEIGHT_SEMANTIC_SIM,
    w3: float = WEIGHT_LLM_SCORE,
    w4: float = WEIGHT_VES,
    table_order_sensitive: bool = TABLE_SIM_ORDER_SENSITIVE,
    table_order_mismatch_weight: float = TABLE_SIM_ORDER_MISMATCH_WEIGHT,
) -> tuple[List[MetricResult], dict]:
    """Run full benchmark on test cases.

    For each test case:
    1. Execute both queries (with timing) and calculate EX
    2. Calculate S_C (semantic similarity using embeddings)
    3. Calculate S_T (table similarity using edit distance)
    4. Calculate LLM Score (LLM-as-judge evaluation)
    5. Calculate VES (valid efficiency score)
    6. Calculate Composite Score (weighted combination)

    Args:
        test_cases: List of test cases to benchmark
        db_executor: Database executor for query execution
        openai_client: OpenAI-compatible client for embeddings and LLM
        w1: Weight for table similarity (S_T)
        w2: Weight for semantic similarity (S_C)
        w3: Weight for LLM score
        w4: Weight for VES
        table_order_sensitive: Order-sensitive table similarity mode
        table_order_mismatch_weight: Soft order mismatch penalty in
            order-insensitive mode

    Returns:
        Tuple of (results, summary_stats)
    """
    results = []
    start_time = time.time()

    for i, test_case in enumerate(test_cases):
        test_start = time.time()

        # Step 1: Execute queries with individual timing
        gen_start = time.time()
        result_gen = db_executor.execute(test_case.generated_sql)
        exec_time_gen_ms = (time.time() - gen_start) * 1000

        ref_start = time.time()
        result_ref = db_executor.execute(test_case.expected_sql)
        exec_time_ref_ms = (time.time() - ref_start) * 1000

        # Step 2a: Select intent-relevant columns for evaluation
        selection = select_relevant_columns(
            test_case.natural_language,
            test_case.generated_sql,
            test_case.expected_sql,
            result_gen,
            result_ref,
            openai_client,
        )

        projected_gen = _project_result_for_comparison(
            result_gen,
            {col: col for col in selection["generated_columns"]},
        )
        projected_ref = _project_result_for_comparison(
            result_ref,
            {
                gen_col: exp_col
                for gen_col, exp_col in selection["column_mapping"].items()
            },
        )

        if not selection["column_mapping"]:
            projected_gen = result_gen
            projected_ref = result_ref

        # Step 1b: Calculate EX (needed for VES)
        ex = (
            projected_gen.succeeded
            and projected_ref.succeeded
            and len(projected_gen.rows) == len(projected_ref.rows)
            and set(projected_gen.columns) == set(projected_ref.columns)
            and projected_gen.rows == projected_ref.rows
        )

        # Step 2: Calculate semantic similarity (S_C)
        semantic_sim = calculate_semantic_similarity(
            test_case.generated_sql,
            test_case.expected_sql,
            openai_client,
        )

        # Step 3: Calculate table similarity (S_T)
        table_sim = _calculate_table_similarity_from_results(
            projected_gen,
            projected_ref,
            order_sensitive=table_order_sensitive,
            order_mismatch_weight=table_order_mismatch_weight,
        )

        # Apply penalty if execution failed
        if not result_gen.succeeded:
            table_sim *= 1 - EXECUTION_FAILURE_PENALTY

        # Step 4: Calculate LLM Score
        llm_score, llm_reasoning = calculate_llm_score(
            test_case.natural_language,
            test_case.generated_sql,
            test_case.expected_sql,
            result_gen,
            result_ref,
            openai_client,
        )

        # Step 5: Calculate VES
        ves = calculate_ves(ex, exec_time_gen_ms, exec_time_ref_ms)

        # Step 6: Calculate Composite Score
        composite = calculate_composite_score(
            table_sim,
            semantic_sim,
            llm_score,
            ves,
            w1,
            w2,
            w3,
            w4,
        )

        # Record result
        test_time = (time.time() - test_start) * 1000  # Convert to ms
        result = MetricResult(
            test_case=test_case,
            semantic_sim=semantic_sim,
            table_sim=table_sim,
            llm_score=llm_score,
            llm_reasoning=llm_reasoning,
            ves=ves,
            composite_score=composite,
            selected_generated_columns=selection["generated_columns"],
            selected_expected_columns=selection["expected_columns"],
            column_selection_confidence=selection["confidence"],
            column_selection_source=selection["source"],
            execution_time_gen_ms=exec_time_gen_ms,
            execution_time_ref_ms=exec_time_ref_ms,
            execution_time_ms=test_time,
        )
        results.append(result)

        print(
            f"[{i+1}/{len(test_cases)}] "
            f"S_C={semantic_sim:.3f} S_T={table_sim:.3f} "
            f"LLM={llm_score:.3f} VES={ves:.3f} "
            f"Composite={composite:.3f} "
            f"Cols={selection['generated_columns']} "
            f"(time: {test_time:.1f}ms)"
        )

    # Calculate summary statistics
    total_time = (time.time() - start_time) * 1000

    summary_stats = {
        "total_tests": len(test_cases),
        "avg_semantic_sim": (
            sum(r.semantic_sim for r in results) / len(results) if results else 0
        ),
        "avg_table_sim": (
            sum(r.table_sim for r in results) / len(results) if results else 0
        ),
        "avg_llm_score": (
            sum(r.llm_score for r in results) / len(results) if results else 0
        ),
        "avg_ves": (sum(r.ves for r in results) / len(results) if results else 0),
        "avg_composite_score": (
            sum(r.composite_score for r in results) / len(results) if results else 0
        ),
        "total_time_ms": total_time,
        "w1": w1,
        "w2": w2,
        "w3": w3,
        "w4": w4,
    }

    return results, summary_stats
