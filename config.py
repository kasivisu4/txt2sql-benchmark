"""Configuration and constants for txt2sql benchmark suite."""

# Default QAS weighting parameter
# QAS = (1 - DEFAULT_QAS_WEIGHT) * semantic_sim + DEFAULT_QAS_WEIGHT * table_sim
# 0.3 means: 70% semantic similarity, 30% table similarity
DEFAULT_QAS_WEIGHT = 0.3

# LM Studio configuration
# LM Studio is OpenAI-compatible local inference server
# Default endpoint: http://localhost:1234/v1
LM_STUDIO_API_URL = "http://127.0.0.1:11434/v1"

# SQLite database used for query execution tests
SQLITE_DB_PATH = "sakila.db"

# Model to use for embeddings (whatever is loaded in LM Studio)
# Common options: "llama-2-7b", "mistral", "neural-chat", etc.
# This should match a model currently loaded in LM Studio
EMBEDDING_MODEL = "text-embedding-qwen3-embedding-8b"

# Chat-capable model used to select the columns that matter for evaluation.
# If unavailable, the benchmark falls back to schema-based heuristics.
COLUMN_SELECTION_LLM_ENABLED = True
COLUMN_SELECTION_MODEL = "qwen2.5-7b-instruct"
COLUMN_SELECTION_TEMPERATURE = 0.0

# Penalty factor when table execution fails (applies to QAS calculation)
EXECUTION_FAILURE_PENALTY = 0.2  # Reduce QAS by 20% if execution fails

# QAS penalty for expected output columns that are missing from the generated output.
# Penalty applied as: weight * (missing_expected_columns / total_expected_columns)
MISSING_COLUMN_PENALTY_WEIGHT = 0.15

# Table similarity behavior.
# False: order-insensitive (default)
# True: order-sensitive
TABLE_SIM_ORDER_SENSITIVE = False

# Optional penalty for row-order mismatch when running in order-insensitive mode.
# 0.0 keeps fully order-insensitive behavior.
# Example: 0.2 adds a soft penalty when values are the same set but shuffled.
TABLE_SIM_ORDER_MISMATCH_WEIGHT = 0.0

# Timeout for query execution (seconds)
QUERY_TIMEOUT = 30

# ============================================================================
# Test Data Configuration
# ============================================================================

MOCK_DATABASE_QUERIES = [
    {
        "id": 1,
        "query": "SELECT id, name FROM users WHERE age > 30",
        "result": {
            "succeeded": True,
            "columns": ["id", "name"],
            "rows": [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
                {"id": 5, "name": "David"},
            ],
        },
    },
    {
        "id": 2,
        "query": "SELECT department, COUNT(*) as count FROM employees GROUP BY department",
        "result": {
            "succeeded": True,
            "columns": ["department", "count"],
            "rows": [
                {"department": "Sales", "count": 5},
                {"department": "Engineering", "count": 8},
                {"department": "HR", "count": 2},
            ],
        },
    },
    {
        "id": 3,
        "query": "SELECT * FROM products WHERE price < 100",
        "result": {
            "succeeded": True,
            "columns": ["id", "name", "price"],
            "rows": [
                {"id": 10, "name": "Widget", "price": 45.99},
                {"id": 11, "name": "Gadget", "price": 67.50},
            ],
        },
    },
]
