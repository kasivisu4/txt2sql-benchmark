"""Data models and utility functions for txt2sql benchmark suite."""

from dataclasses import dataclass, field
from typing import List, Dict, Any
import re
import math


@dataclass
class TestCase:
    """Represents a single test case."""

    natural_language: str
    generated_sql: str
    expected_sql: str


@dataclass
class QueryResult:
    """Represents the result of query execution."""

    rows: List[Dict[str, Any]]
    columns: List[str]
    succeeded: bool
    error_message: str = ""


@dataclass
class MetricResult:
    """Represents computed metrics for a single test case."""

    test_case: TestCase
    em: bool  # Exact Match (binary)
    ex: bool  # Execution Accuracy (binary)
    semantic_sim: float  # Semantic similarity [0, 1]
    table_sim: float  # Table similarity [0, 1]
    qas: float  # Query Affinity Score [0, 1]
    selected_generated_columns: List[str] = field(default_factory=list)
    selected_expected_columns: List[str] = field(default_factory=list)
    missing_expected_columns: List[str] = field(default_factory=list)
    missing_column_penalty: float = 0.0
    column_selection_confidence: float = 0.0
    column_selection_source: str = "fallback"
    execution_time_ms: float = 0.0


@dataclass
class BenchmarkReport:
    """Aggregated benchmark results."""

    results: List[MetricResult] = field(default_factory=list)
    summary_stats: Dict[str, float] = field(default_factory=dict)
    total_time_ms: float = 0.0
    weight: float = 0.3


# ============================================================================
# Utility Functions
# ============================================================================


def normalize_sql(sql: str) -> str:
    """Normalize SQL string for comparison.

    - Trim whitespace
    - Lowercase
    - Normalize multiple spaces to single space
    - Remove trailing semicolons
    """
    sql = sql.strip().lower()
    sql = re.sub(r"\s+", " ", sql)  # Multiple spaces -> single space
    sql = sql.rstrip(";")  # Remove trailing semicolon
    return sql


def edit_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings.

    Represents minimum edits (insert, delete, replace) needed to transform s1 to s2.
    """
    if len(s1) < len(s2):
        return edit_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # j+1 instead of j since previous_row and current_row are one character longer
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Returns value in [0, 1].
    """
    if len(v1) != len(v2):
        raise ValueError("Vectors must have same length")

    if len(v1) == 0:
        return 0.0

    dot_product = sum(a * b for a, b in zip(v1, v2))
    magnitude_v1 = math.sqrt(sum(a**2 for a in v1))
    magnitude_v2 = math.sqrt(sum(b**2 for b in v2))

    if magnitude_v1 == 0 or magnitude_v2 == 0:
        return 0.0

    return dot_product / (magnitude_v1 * magnitude_v2)


def results_equal(result1: QueryResult, result2: QueryResult) -> bool:
    """Check if two query results are equal.

    Compares:
    - Both succeeded
    - Same number of rows
    - Same columns (order-independent)
    - Same data
    """
    if not result1.succeeded or not result2.succeeded:
        return False

    if len(result1.rows) != len(result2.rows):
        return False

    if set(result1.columns) != set(result2.columns):
        return False

    # For simplicity, compare rows as-is (order matters)
    return result1.rows == result2.rows
