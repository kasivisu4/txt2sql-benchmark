# txt2sql Benchmark Suite - Architecture

## Overview

The txt2sql Benchmark Suite is a lightweight Python application for evaluating text-to-SQL generation models. It computes multiple evaluation metrics and exports detailed results to Excel.

**Design Philosophy**: Minimal, modular, and extensible. Just 5 core files with clear responsibilities.

## System Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                         main.py                                 │
│  Orchestrates the pipeline: load → compute → report             │
└────────┬───────────────────────┬───────────────────────┬────────┘
         │                       │                       │
    ┌────▼─────┐         ┌──────▼────────┐    ┌────────▼────────┐
    │ model.py  │         │  metric.py    │    │ mock_database.py │
    │(data      │         │ (calculators) │    │ (executor)       │
    │structures)│         │               │    │                  │
    └──────┬────┘         └──────┬────────┘    └───────┬──────────┘
           │                     │                     │
           └─────────────────┬───┴─────────────────────┘
                             │
                        ┌────▼──────────────┐
                        │  config.py        │
                        │  (configuration)  │
                        └───────────────────┘
```

### File-by-File Responsibilities

#### 1. **model.py** — Data Models & Utilities

Defines all data structures and utility functions.

**Data Classes:**
- `TestCase` — Input: {natural_language, generated_sql, expected_sql}
- `QueryResult` — Database query output: {rows, columns, succeeded, error_message}
- `MetricResult` — Computed metrics: {em, ex, semantic_sim, table_sim, qas, execution_time_ms}
- `BenchmarkReport` — Aggregated results: {results[], summary_stats{}, total_time_ms, weight}

**Utility Functions:**
- `normalize_sql(sql)` — Lowercase, trim, normalize whitespace for SQL comparison
- `edit_distance(s1, s2)` — Levenshtein distance for table column comparison
- `cosine_similarity(v1, v2)` — Vector similarity [0,1]
- `results_equal(r1, r2)` — Check if two query results match

#### 2. **config.py** — Configuration & Constants

Centralized configuration for the entire suite.

**Key Settings:**
- `DEFAULT_QAS_WEIGHT = 0.3` — Default w for QAS formula
- `LM_STUDIO_API_URL` — Endpoint for embedding service
- `EMBEDDING_MODEL` — Model name (ignored by LM Studio, uses loaded model)
- `EXECUTION_FAILURE_PENALTY` — Penalty applied if query execution fails
- `MOCK_DATABASE_QUERIES` — Hardcoded 3 test queries with expected results

**Why centralize:** Easy to adjust without modifying code logic

#### 3. **metric.py** — Metric Calculators

Implements all metric calculations for the benchmark.

**Functions:**

1. **`calculate_em(generated_sql, expected_sql) → bool`**
   - Compares normalized SQL strings
   - EM = 1 if match, 0 otherwise

2. **`calculate_ex(result_gen, result_ref) → bool`**
   - Compares query results
   - EX = 1 if both executed and results match, 0 otherwise
   - Returns 0 if either query failed

3. **`calculate_semantic_similarity(generated_sql, expected_sql, openai_client) → float [0,1]`**
   - Calls LM Studio to get embeddings for both queries
   - Computes cosine similarity
   - Optimization: returns 1.0 if SQLs match exactly
   - Clamps output to [0,1]

4. **`calculate_table_similarity(generated_sql, expected_sql, db_executor) → float [0,1]`**
   
   Algorithm:
   ```
   Step 1: If SQLs match exactly → return 1.0
   Step 2: Execute both queries
   Step 3: If generated query fails → return 0.0
   Step 4: For each column in generated result:
       - Find minimum edit distance to any reference column
       - Normalize distance by max(rows_gen, rows_ref)
   Step 5: Aggregate distances and compute final similarity
       - S_T = 1.0 - (total_distance / max_cols)
   ```
   - Handles edge cases (empty results, execution failures)
   - Returns [0,1] clamped value

5. **`calculate_qas(semantic_sim, table_sim, weight) → float [0,1]`**
   - Combines semantic and table similarity with weighting
   - Formula: `QAS = (1-weight) * semantic_sim + weight * table_sim`
   - Default weight=0.3 (70% semantic, 30% table)
   - User-configurable via CLI flag

6. **`run_benchmark(test_cases, db_executor, openai_client, weight) → (List[MetricResult], dict)`**
   
   Orchestrates full pipeline:
   ```
   For each test case:
     1. Calculate EM
     2. Execute queries for EX
     3. Calculate semantic similarity (S_C)
     4. Calculate table similarity (S_T)
     5. Apply execution failure penalty if needed
     6. Calculate QAS combining C_C and S_T
   
   Aggregate statistics:
     - Pass rates (EM, EX)
     - Average scores (semantic, table, QAS)
     - Total execution time
   ```

#### 4. **mock_database.py** — Query Executor

Executes SQL queries and returns results.

**Class: `MockDatabaseExecutor`**
- `__init__()` — Load hardcoded queries from config
- `execute(query)` → QueryResult
  - Normalize and match query against mock database
  - Return QueryResult with rows/columns on match
  - Return error on failure
- `_normalize_query()` — Normalize SQL for matching
- `_queries_similar()` — Check if two queries match

**Current Limitation:** Supports only 3 hardcoded test queries (from config.py)

**Extension Pattern:** Replace with custom executor that connects to real database

#### 5. **main.py** — Entry Point & Reporting

Main application orchestrator and Excel report generator.

**Functions:**

1. **`load_test_cases(json_file) → List[TestCase]`**
   - Parse JSON input file
   - Validate structure
   - Return list of test cases

2. **`export_to_excel(results, summary_stats, output_file)`**
   - Create Excel workbook
   - Populate 3 sheets
   - Save to output file

3. **`_populate_summary_sheet(ws, summary_stats, results)`**
   - Sheet 1: Summary statistics
   - Shows: EM/EX/QAS averages, pass rates, timing

4. **`_populate_results_sheet(ws, results)`**
   - Sheet 2: Per-query results
   - One row per test case
   - Shows all computed metrics

5. **`_populate_info_sheet(ws, summary_stats)`**
   - Sheet 3: Configuration and instructions
   - Documents current weight
   - Explains QAS formula
   - Shows examples for different weights

6. **`main()`**
   - Parse command-line arguments
   - Load test cases
   - Initialize OpenAI client for LM Studio
   - Initialize mock database executor
   - Call `run_benchmark()`
   - Export Excel report
   - Print console summary

## Data Flow

### Execution Pipeline

```
User runs: python main.py --input data/test_cases.json --weight 0.3
                                    │
                                    ▼
                    Load JSON test cases
                                    │
                                    ▼
                    Initialize LM Studio client
                    (OpenAI-compatible API)
                                    │
                                    ▼
                    Initialize MockDatabaseExecutor
                                    │
                                    ▼
            ╔═══════════════════════════════════════╗
            ║   For each TestCase:                  ║
            ║   1. Calculate EM (exact match)       ║
            ║   2. Execute queries → Calculate EX   ║
            ║   3. Get embeddings → Calculate S_C   ║
            ║   4. Compare tables → Calculate S_T   ║
            ║   5. Compute QAS = (1-w)*S_C + w*S_T  ║
            ╚═════════════════╤══════════════════════╝
                              │
                              ▼
                    Aggregate summary stats
                              │
                              ▼
                    Generate Excel report
                    (Summary, Results, Info sheets)
                              │
                              ▼
                   Save report to results/
                              │
                              ▼
                    Print console summary
```

### Metric Computation Details

#### Exact Match (EM)

```
normalize(generated_sql) == normalize(expected_sql) → 1 or 0
```

Normalization:
- Lowercase
- Trim whitespace
- Normalize multiple spaces to single space
- Remove trailing semicolon

#### Execution Accuracy (EX)

```
if generated_result.succeeded AND expected_result.succeeded:
    if generated_result.rows == expected_result.rows:
        return 1
return 0
```

#### Semantic Similarity (S_C)

```
embedding_gen = LM_Studio.embed(generated_sql)
embedding_ref = LM_Studio.embed(expected_sql)
S_C = cosine_similarity(embedding_gen, embedding_ref)
```

Range: [0, 1]
- 1.0 = identical semantics
- 0.0 = completely different

#### Table Similarity (S_T)

```
if sql_gen == sql_ref (normalized):
    return 1.0

result_gen = execute(sql_gen)
result_ref = execute(sql_ref)

if result_gen.failed:
    return 0.0

max_rows = max(len(result_gen.rows), len(result_ref.rows))
total_distance = 0

for each col in result_gen.columns:
    min_edit_distance = infinity
    for each ref_col in result_ref.columns:
        distance = edit_distance(col_values, ref_col_values)
        min_edit_distance = min(min_edit_distance, distance)
    
    normalized_dist = min_edit_distance / max_rows
    total_distance += normalized_dist

S_T = 1.0 - (total_distance / max(len(gen_cols), len(ref_cols)))
return clamp(S_T, 0, 1)
```

Range: [0, 1]
- 1.0 = identical results
- 0.0 = completely different results

#### Query Affinity Score (QAS)

```
QAS = (1 - w) * S_C + w * S_T

where:
  w = weighting parameter (default 0.3)
  S_C = semantic similarity
  S_T = table similarity
```

Default interpretation (w=0.3):
- 70% weight on semantic similarity (code structure)
- 30% weight on table similarity (execution results)

Range: [0, 1]

## Configuration

All configuration is in `config.py`:

```python
DEFAULT_QAS_WEIGHT = 0.3              # QAS weight parameter
LM_STUDIO_API_URL = "http://..."      # LM Studio endpoint
EMBEDDING_MODEL = "llama-2-7b"        # Model name (for reference)
EXECUTION_FAILURE_PENALTY = 0.2       # Penalty if query fails
QUERY_TIMEOUT = 30                    # Timeout in seconds
MOCK_DATABASE_QUERIES = [...]         # Hardcoded test queries
```

## Extensibility

### Adding Real Database Support

Replace MockDatabaseExecutor in main.py:

```python
# Instead of:
db_executor = MockDatabaseExecutor()

# Use:
from your_db_module import YourDatabaseExecutor
db_executor = YourDatabaseExecutor(connection_string="...")
```

Your executor must implement:
```python
class YourDatabaseExecutor:
    def execute(self, query: str) -> QueryResult:
        # Execute query
        # Return QueryResult
        pass
```

### Changing Embedding Provider

Modify `metric.py`:

```python
def calculate_semantic_similarity(...):
    # Instead of LM Studio:
    # emb = openai_client.embeddings.create(...)
    
    # Use HuggingFace:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('code-embedding-model')
    embedding = model.encode(sql)
```

### Adding New Metrics

1. Add function to `metric.py`
2. Call from `run_benchmark()`
3. Add result field to `MetricResult` in `model.py`
4. Update Excel export in `main.py`

## Performance Characteristics

**Bottleneck:** LM Studio embedding API calls

Typical times:
- 3 test cases: 1-2 seconds
- 100 test cases: 30-60 seconds
- 1000 test cases: 5-10 minutes

**Optimization opportunities:**
- Embedding caching (cache embeddings locally)
- Batch API calls (if LM Studio supports)
- Parallel query execution

## Dependencies

```
openai>=1.0.0       # OpenAI-compatible client for LM Studio
openpyxl>=3.10.0    # Excel workbook creation
numpy>=1.24.0       # Numerical operations (for cosine similarity)
```

## Error Handling

**LM Studio connection failures:**
- Logged as warning
- Benchmark continues with fallback (similarity = 0)

**Query execution failures:**
- EX set to 0
- Table similarity penalized (reduced by EXECUTION_FAILURE_PENALTY)

**Missing input file:**
- Program exits with error message

**Excel export failures:**
- Program exits with traceback

## Testing

Basic integration test:
```bash
# Run with default sample data
python main.py

# Verify output:
# - results/benchmark_report.xlsx is created
# - Contains 3 sheets (Summary, Results, Info)
# - Metrics are in expected ranges
```

## Future Enhancements

1. **Embedding Caching** — Cache embeddings to avoid recomputation
2. **Batch API Calls** — Reduce latency for large test sets
3. **Real Database Support** — Add drivers for PostgreSQL, MySQL, etc.
4. **Advanced Metrics** — BLEU, ROUGE, or other NLP metrics
5. **Visualization** — Dashboard instead of just Excel
6. **Parallel Execution** — Process multiple queries concurrently
7. **Diff View** — Show side-by-side differences in Excel
