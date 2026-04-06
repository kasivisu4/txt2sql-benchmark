# txt2sql Benchmark Suite

A lightweight benchmarking suite for evaluating text-to-SQL generation systems against a real SQLite database using Exact Match, Execution Accuracy, Semantic Similarity, Table Similarity, and an intent-aware Query Affinity Score.

## Overview

This benchmark suite computes the following metrics for each test case:

| Metric | Type | Definition |
|--------|------|-----------|
| **EM (Exact Match)** | Binary (0/1) | Whether normalized generated and expected SQL strings match exactly |
| **EX (Execution Accuracy)** | Binary (0/1) | Whether the intent-relevant projected query results are identical |
| **S_C (Semantic Similarity)** | Continuous [0,1] | Cosine similarity of SQL code embeddings |
| **S_T (Table Similarity)** | Continuous [0,1] | Intent-aware edit distance-based similarity of result table columns |
| **QAS (Query Affinity Score)** | Continuous [0,1] | `(1-w)*S_C + w*S_T - MissingColumnPenalty` |

### Key Features

- **SQLite-backed execution** — runs generated and expected SQL against `sakila.db`
- **LM Studio integration** — uses OpenAI-compatible API for embeddings and optional column judging
- **Intent-aware evaluation** — selects only the columns needed to answer the natural-language question
- **Order handling controls** — supports order-insensitive comparison with optional row-order penalty
- **Missing-column penalty** — lowers QAS when generated output omits expected columns
- **Excel reporting** — rich multi-sheet reports with summary statistics
- **Configurable weighting** — adjust QAS weight via CLI flag
- **Semantic + Table metrics** — accounts for both SQL similarity and execution output quality

## Architecture

### Files

```
model.py               # Data models and utility helpers
config.py              # Runtime configuration and scoring weights
metric.py              # Metric calculators and intent-aware column selection
mock_database.py       # SQLite executor used for Sakila benchmarking
main.py                # CLI entry point and Excel export
requirements.txt       # Python dependencies
README.md              # This file
ARCHITECTURE.md        # Detailed architecture documentation
data/                  # Input benchmark cases (JSON)
results/               # Generated Excel reports
sakila.db              # SQLite database used for execution testing
```

### Metric Computation Flow

```
For each test case:
┌─────────────────────────────────────────────────────────────┐
│ 1. Exact Match (EM)                                         │
│    - Normalize both SQL strings (lowercase, trim, etc.)     │
│    - Compare for exact equality → binary (0/1)              │
├─────────────────────────────────────────────────────────────┤
│ 2. Execute both queries on sakila.db                        │
│    - Get generated and expected result sets                 │
├─────────────────────────────────────────────────────────────┤
│ 3. Intent-Aware Column Selection                            │
│    - Use LM Studio chat model, or fallback heuristics       │
│    - Select only columns required by the user query         │
├─────────────────────────────────────────────────────────────┤
│ 4. Execution Accuracy (EX)                                  │
│    - Compare projected result rows → binary (0/1)           │
├─────────────────────────────────────────────────────────────┤
│ 5. Semantic Similarity (S_C)                                │
│    - Get embeddings from LM Studio for both SQLs            │
│    - Compute cosine similarity → [0,1]                      │
├─────────────────────────────────────────────────────────────┤
│ 6. Table Similarity (S_T)                                   │
│    - Compare projected relevant result columns              │
│      - Find minimum edit distance to reference columns      │
│      - Optionally penalize row-order mismatch               │
│    - Aggregate across all columns → [0,1]                  │
├─────────────────────────────────────────────────────────────┤
│ 7. Query Affinity Score (QAS)                              │
│    - QAS = (1-w)*S_C + w*S_T - missing-column penalty      │
│    - Default w=0.3 (70% semantic, 30% table)               │
└─────────────────────────────────────────────────────────────┘
```

## Setup

### Prerequisites

- **Python 3.11+**
- **LM Studio** running with:
  - an embedding model for semantic similarity
  - optionally a chat/instruction model for intent-aware column selection
  - Download: https://lmstudio.ai/
  - Start the local API server on the configured endpoint
- **sakila.db** present in the project root

### Installation

1. Clone or download the project
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Basic Usage

```bash
# Run with default settings (data/sakila_test_cases.json)
python main.py

# Use custom input file
python main.py --input data/my_test_cases.json

# Specify output file
python main.py --output results/my_report.xlsx

# Adjust QAS weight
python main.py --weight 0.5

# Enable strict row-order comparison for table similarity
python main.py --table-order-sensitive

# Add a soft penalty when rows are shuffled
python main.py --table-order-mismatch-weight 0.25
```

### Input Format

Create a JSON file with test cases:

```json
[
  {
    "natural_language": "Find users older than 30",
    "generated_sql": "SELECT * FROM users WHERE age > 30",
    "expected_sql": "SELECT * FROM users WHERE age > 30"
  },
  {
    "natural_language": "Count employees by department",
    "generated_sql": "SELECT department, COUNT(*) FROM employees GROUP BY dept",
    "expected_sql": "SELECT department, COUNT(*) FROM employees GROUP BY department"
  }
]
```

### Output Format

The tool generates an Excel file with 3 sheets:

**Sheet 1: Summary**
- Total number of tests
- EM pass rate, EX pass rate
- Average semantic similarity, table similarity, missing-column penalty, QAS
- Total execution time
- Current QAS weight

**Sheet 2: Results**
- One row per test case
- Columns include intent-aware evaluation columns, missing expected columns, penalty, judge source, and confidence
- Shows detailed scoring inputs for each query

**Sheet 3: Info**
- Configuration and instructions
- Metric definitions
- Examples for testing different weights

## Configuring QAS Weight

The Query Affinity Score balances semantic similarity with intent-aware table similarity, then deducts missing expected columns:

```
QAS = (1 - w) * SemanticSim + w * TableSim - MissingColumnPenalty
```

- **w=0.0**: Pure semantic similarity (100% code structure)
- **w=0.3**: Default (70% code, 30% results)
- **w=0.5**: Equal weight (50/50)
- **w=1.0**: Pure table similarity (100% results)

Missing-column penalty is applied when the generated result omits expected output columns. Extra generated columns are currently tolerated by the intent-aware column selector unless you add a separate penalty.

## Intent-Aware Evaluation

For each test case, the benchmark first determines which result columns are actually needed to answer the natural-language question.

Example:

```json
{
  "natural_language": "Count category wise movies",
  "generated_sql": "SELECT c.name, c.category_id, COUNT(fc.film_id) AS film_count ...",
  "expected_sql": "SELECT c.name, COUNT(fc.film_id) AS film_count ..."
}
```

The relevant answer columns are usually:

```text
name, film_count
```

So `category_id` can be ignored for EX and S_T if it is not required to answer the user query. This makes evaluation closer to user intent rather than raw schema equality.

If the LM Studio judge model is unavailable, the benchmark falls back to matching common columns heuristically.

## Table Similarity Modes

By default, table similarity is order-insensitive. This means row value shuffles do not automatically lower S_T.

Available options:

```bash
# default: order-insensitive
python main.py

# strict order-sensitive mode
python main.py --table-order-sensitive

# hybrid mode: mostly order-insensitive, but penalize row shuffles
python main.py --table-order-mismatch-weight 0.25
```

To test different weights, re-run with the `--weight` parameter:

```bash
python main.py --weight 0.1    # 90% semantic, 10% table
python main.py --weight 0.5    # 50% semantic, 50% table
python main.py --weight 0.9    # 10% semantic, 90% table
```

## LM Studio Integration

This suite uses **LM Studio** for embeddings and optional column-selection judging. LM Studio provides an OpenAI-compatible local API.

### Setup LM Studio

1. Download and install LM Studio from https://lmstudio.ai/
2. Load an embedding model for semantic similarity
3. Optionally load a chat/instruction model for column selection
3. Start the Local API Server (default: `http://localhost:1234/v1`)
4. The suite will auto-connect to the configured base URL

Configured models live in `config.py`.

## Extending the Suite

### Using Another Database

Replace the SQLite executor in `mock_database.py` with your own executor:

```python
class YourDBExecutor:
    def execute(self, query: str) -> QueryResult:
        # Connect to your database
        # Execute and return QueryResult
        pass
```

### Using Different Models

Update the configured model names in `config.py`:

```python
EMBEDDING_MODEL = "..."
COLUMN_SELECTION_MODEL = "..."
```

If you do not want LLM-based column selection:

```python
COLUMN_SELECTION_LLM_ENABLED = False
```

### Adding New Metrics

Add new functions to `metric.py` and update `BenchmarkRunner.run_benchmark()`:

```python
def calculate_custom_metric(gen_result, ref_result) -> float:
    # Your logic here
    return score
```

## Troubleshooting

### "Could not connect to LM Studio"

- Check LM Studio is running: `http://localhost:1234/v1`
- Ensure a model is loaded in LM Studio
- The suite will continue with fallback (similarity = 0)

### Column selection model not loaded

- The benchmark falls back to common-column heuristics
- Load the configured chat model in LM Studio to enable full intent-aware judging

### SQLite execution fails

- Ensure `sakila.db` exists in the project root
- Ensure the SQL syntax is valid for SQLite

### Excel file could not be created

- Check write permissions in the `results/` directory
- Ensure the directory exists

## Performance

- Bottlenecks are LM Studio API calls and SQL execution on larger query sets
- Intent-aware judging adds one extra LLM call per test case when enabled

## License

MIT License

## Support

For issues or questions, refer to `ARCHITECTURE.md` for detailed design documentation.
