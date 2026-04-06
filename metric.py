"""Metric calculators for txt2sql benchmark suite.

Computes:
- EM (Exact Match): Binary comparison of normalized SQL strings
- EX (Execution Accuracy): Binary comparison of query results
- S_C (Semantic Similarity): Cosine similarity of code embeddings from LM Studio
- S_T (Table Similarity): Edit distance-based similarity of result tables
- QAS (Query Affinity Score): Weighted combination of S_C and S_T
"""

import json
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
    DEFAULT_QAS_WEIGHT,
    EMBEDDING_MODEL,
    MISSING_COLUMN_PENALTY_WEIGHT,
    TABLE_SIM_ORDER_SENSITIVE,
    TABLE_SIM_ORDER_MISMATCH_WEIGHT,
    COLUMN_SELECTION_LLM_ENABLED,
    COLUMN_SELECTION_MODEL,
    COLUMN_SELECTION_TEMPERATURE,
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
            col for col in payload.get("generated_columns", []) if col in result_gen.columns
        ]
        expected_columns = [
            col for col in payload.get("expected_columns", []) if col in result_ref.columns
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


def calculate_missing_column_penalty(
    result_gen: QueryResult,
    result_ref: QueryResult,
    penalty_weight: float = MISSING_COLUMN_PENALTY_WEIGHT,
) -> tuple[list[str], float]:
    """Calculate QAS penalty for expected columns missing from generated output."""
    if not result_ref.columns:
        return [], 0.0

    missing_expected_columns = [
        col for col in result_ref.columns if col not in result_gen.columns
    ]
    penalty_ratio = len(missing_expected_columns) / max(1, len(result_ref.columns))
    penalty = penalty_weight * penalty_ratio
    return missing_expected_columns, max(0.0, min(1.0, penalty))


def calculate_em(generated_sql: str, expected_sql: str) -> bool:
    """Calculate Exact Match (EM).

    Compares normalized SQL strings for exact equality.

    Args:
        generated_sql: Generated SQL query
        expected_sql: Expected (reference) SQL query

    Returns:
        True if normalized SQLs match, False otherwise
    """
    gen_norm = normalize_sql(generated_sql)
    exp_norm = normalize_sql(expected_sql)
    return gen_norm == exp_norm


def calculate_ex(result_gen: QueryResult, result_ref: QueryResult) -> bool:
    """Calculate Execution Accuracy (EX).

    Compares query execution results.
    If either query failed, returns False.
    Otherwise, returns True if results are identical.

    Args:
        result_gen: Generated query result
        result_ref: Reference query result

    Returns:
        True if both executed successfully and results match, False otherwise
    """
    if not result_gen.succeeded or not result_ref.succeeded:
        return False

    if len(result_gen.rows) != len(result_ref.rows):
        return False

    if set(result_gen.columns) != set(result_ref.columns):
        return False

    return result_gen.rows == result_ref.rows


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


def calculate_table_similarity(
    generated_sql: str,
    expected_sql: str,
    db_executor: MockDatabaseExecutor,
    order_sensitive: bool = TABLE_SIM_ORDER_SENSITIVE,
    order_mismatch_weight: float = TABLE_SIM_ORDER_MISMATCH_WEIGHT,
) -> float:
    """Calculate table similarity using edit distance on result columns.

    Algorithm:
    1. Return 1.0 if SQLs match exactly
    2. Execute both queries; return 0.0 if generated fails
    3. For each column in generated result, find best-match reference column
    4. Aggregate and normalize edit distances

    Args:
        generated_sql: Generated SQL query
        expected_sql: Expected SQL query
        db_executor: Database executor (mock or real)
        order_sensitive: Whether to preserve row order during comparison
        order_mismatch_weight: Soft penalty weight for shuffled order in
            order-insensitive mode (0 disables penalty)

    Returns:
        Table similarity score in range [0, 1]
    """
    # Step 1: Check exact match
    if normalize_sql(generated_sql) == normalize_sql(expected_sql):
        return 1.0

    # Step 2: Execute both queries
    result_gen = db_executor.execute(generated_sql)
    result_ref = db_executor.execute(expected_sql)

    return _calculate_table_similarity_from_results(
        result_gen,
        result_ref,
        order_sensitive=order_sensitive,
        order_mismatch_weight=order_mismatch_weight,
    )


def calculate_qas(
    semantic_sim: float,
    table_sim: float,
    weight: float = DEFAULT_QAS_WEIGHT,
    missing_column_penalty: float = 0.0,
) -> float:
    """Calculate Query Affinity Score (QAS).

    Formula: QAS = (1 - weight) * semantic_sim + weight * table_sim

    Default weight=0.3 means 70% semantic, 30% table similarity.

    Args:
        semantic_sim: Semantic similarity score [0, 1]
        table_sim: Table similarity score [0, 1]
        weight: Weighting parameter (default 0.3)
        missing_column_penalty: Penalty deducted for missing expected columns

    Returns:
        QAS score in range [0, 1]
    """
    qas = (1 - weight) * semantic_sim + weight * table_sim - missing_column_penalty
    return max(0.0, min(1.0, qas))  # Clamp to [0, 1]


def run_benchmark(
    test_cases: List[TestCase],
    db_executor: MockDatabaseExecutor,
    openai_client,
    weight: float = DEFAULT_QAS_WEIGHT,
    table_order_sensitive: bool = TABLE_SIM_ORDER_SENSITIVE,
    table_order_mismatch_weight: float = TABLE_SIM_ORDER_MISMATCH_WEIGHT,
) -> tuple[List[MetricResult], dict]:
    """Run full benchmark on test cases.

    For each test case:
    1. Calculate EM (exact match of SQL)
    2. Execute both queries and calculate EX (execution accuracy)
    3. Calculate S_C (semantic similarity using embeddings)
    4. Calculate S_T (table similarity using edit distance)
    5. Calculate QAS (weighted combination)

    Args:
        test_cases: List of test cases to benchmark
        db_executor: Database executor for query execution
        openai_client: OpenAI-compatible client for embeddings
        weight: QAS weight parameter (default 0.3)
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

        # Step 1: Calculate EM
        em = calculate_em(test_case.generated_sql, test_case.expected_sql)

        # Step 2: Execute queries for EX calculation
        result_gen = db_executor.execute(test_case.generated_sql)
        result_ref = db_executor.execute(test_case.expected_sql)

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

        # Step 2b: Calculate EX
        ex = calculate_ex(projected_gen, projected_ref)

        # Step 3: Calculate semantic similarity
        semantic_sim = calculate_semantic_similarity(
            test_case.generated_sql,
            test_case.expected_sql,
            openai_client,
        )

        # Step 4: Calculate table similarity
        table_sim = _calculate_table_similarity_from_results(
            projected_gen,
            projected_ref,
            order_sensitive=table_order_sensitive,
            order_mismatch_weight=table_order_mismatch_weight,
        )

        # Step 4b: Penalize missing expected columns in generated output
        missing_expected_columns, missing_column_penalty = (
            calculate_missing_column_penalty(result_gen, result_ref)
        )

        # Apply penalty if execution failed
        if not result_gen.succeeded:
            table_sim *= 1 - EXECUTION_FAILURE_PENALTY

        # Step 5: Calculate QAS
        qas = calculate_qas(
            semantic_sim,
            table_sim,
            weight,
            missing_column_penalty=missing_column_penalty,
        )

        # Record result
        test_time = (time.time() - test_start) * 1000  # Convert to ms
        result = MetricResult(
            test_case=test_case,
            em=em,
            ex=ex,
            semantic_sim=semantic_sim,
            table_sim=table_sim,
            qas=qas,
            selected_generated_columns=selection["generated_columns"],
            selected_expected_columns=selection["expected_columns"],
            missing_expected_columns=missing_expected_columns,
            missing_column_penalty=missing_column_penalty,
            column_selection_confidence=selection["confidence"],
            column_selection_source=selection["source"],
            execution_time_ms=test_time,
        )
        results.append(result)

        print(
            f"[{i+1}/{len(test_cases)}] "
            f"EM={em} EX={ex} "
            f"S_C={semantic_sim:.3f} S_T={table_sim:.3f} "
            f"MissPen={missing_column_penalty:.3f} QAS={qas:.3f} "
            f"Cols={selection['generated_columns']} "
            f"(time: {test_time:.1f}ms)"
        )

    # Calculate summary statistics
    total_time = (time.time() - start_time) * 1000

    summary_stats = {
        "total_tests": len(test_cases),
        "em_pass_rate": (
            sum(1 for r in results if r.em) / len(results) if results else 0
        ),
        "ex_pass_rate": (
            sum(1 for r in results if r.ex) / len(results) if results else 0
        ),
        "avg_semantic_sim": (
            sum(r.semantic_sim for r in results) / len(results) if results else 0
        ),
        "avg_table_sim": (
            sum(r.table_sim for r in results) / len(results) if results else 0
        ),
        "avg_missing_column_penalty": (
            sum(r.missing_column_penalty for r in results) / len(results)
            if results
            else 0
        ),
        "avg_qas": sum(r.qas for r in results) / len(results) if results else 0,
        "total_time_ms": total_time,
        "weight": weight,
    }

    return results, summary_stats
