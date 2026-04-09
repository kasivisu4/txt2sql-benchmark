"""Configuration and constants for txt2sql benchmark suite."""

# ============================================================================
# Composite Score Weights
# ============================================================================
# Composite = (W1 * S_T) + (W2 * S_C) + (W3 * LLM_SCORE) + (W4 * VES)
# Weights should sum to 1.0 for a normalized [0, 1] composite score.
WEIGHT_TABLE_SIM = 0.3  # W1: Table Similarity (S_T)
WEIGHT_SEMANTIC_SIM = 0.2  # W2: Semantic Similarity (S_C)
WEIGHT_LLM_SCORE = 0.3  # W3: LLM-as-Judge Score
WEIGHT_VES = 0.2  # W4: Valid Efficiency Score

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
COLUMN_SELECTION_MODEL = "google/gemma-4-26b-a4b"
COLUMN_SELECTION_TEMPERATURE = 0.0

# LLM-as-Judge configuration
# Uses a chat model to score generated SQL on a 0.0-1.0 scale.
LLM_JUDGE_MODEL = "google/gemma-4-26b-a4b"
LLM_JUDGE_TEMPERATURE = 0.0

# Penalty factor when table execution fails (applies to S_T calculation)
EXECUTION_FAILURE_PENALTY = 0.2  # Reduce S_T by 20% if execution fails

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
