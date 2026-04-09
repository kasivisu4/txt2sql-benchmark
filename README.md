# txt2sql Benchmark Suite

Traditional text-to-SQL evaluations rely on Exact Match or Execution Accuracy — binary metrics that fail to capture whether generated SQL truly answers the user's natural-language intent. This suite introduces a **composite scoring framework** that intelligently balances semantic similarity of the SQL code with intent-aware result comparison, LLM-as-judge evaluation, and efficiency scoring — giving you a continuous [0,1] measure of query quality instead of pass/fail.

> Currently uses SQLite (Sakila database) as the execution backend for demonstration, but the core evaluation logic is database-agnostic — it compares query results, not the database engine.

## The Problem

Enterprise-grade text-to-SQL benchmarks like [BIRD](https://bird-bench.github.io/) and Spider evaluate systems with **binary metrics** — Exact Match (EM) and Execution Accuracy (EX). A query either passes or fails, and that's it.

Here are six real scenarios against the **Sakila** database, ranging from trivial to complex:

| # | User Question | Generated SQL | Expected SQL | EM | EX | What binary metrics hide |
|---|--------------|---------------|--------------|----|----|--------------------------|
| 1 | *List all film titles* | `SELECT title FROM film` | `SELECT title FROM film` | 1 | 1 | Perfect match — the easy case binary handles fine |
| 2 | *Count films per category* | `SELECT c.name, COUNT(fc.film_id) FROM category c JOIN film_category fc ON c.category_id = fc.category_id GROUP BY c.name` | `SELECT c.name, count(*) FROM category c JOIN film_category fc ON c.category_id = fc.category_id GROUP BY c.name` | 0 | 1 | SQL differs (`COUNT(fc.film_id)` vs `count(*)`) but produces identical results — EM penalizes a perfectly valid query |
| 3 | *Count films per category* | `SELECT c.name, c.category_id, COUNT(fc.film_id) AS film_count FROM category c JOIN film_category fc ON c.category_id = fc.category_id GROUP BY c.name` | `SELECT c.name, COUNT(fc.film_id) AS film_count FROM category c JOIN film_category fc ON c.category_id = fc.category_id GROUP BY c.name` | 0 | 0 | An extra `category_id` column makes EX=0, but the actual answer (name + count) is 100% correct — our intent-aware column selection would ignore the extra column |
| 4 | *Top 10 customers by rental count* | `SELECT c.first_name, c.last_name, COUNT(r.rental_id) AS rentals FROM customer c JOIN rental r ON c.customer_id = r.customer_id GROUP BY c.customer_id ORDER BY rentals DESC LIMIT 10` | `SELECT c.first_name, c.last_name, COUNT(*) AS rentals FROM customer c JOIN rental r ON c.customer_id = r.customer_id GROUP BY c.first_name, c.last_name ORDER BY rentals DESC LIMIT 10` | 0 | 1 | Different GROUP BY strategy and COUNT style, yet same 10 customers with same counts — EM scores this as completely wrong |
| 5 | *Actors sorted by last name* | `SELECT first_name, last_name FROM actor ORDER BY last_name` | `SELECT first_name, last_name FROM actor ORDER BY last_name DESC` | 0 | 0 | All 200 actors present, all values correct, just reversed sort order — binary treats this equally wrong as querying the wrong table entirely |
| 6 | *Revenue per store* | `SELECT s.store_id, SUM(p.amount) FROM store s JOIN staff st ON s.store_id = st.store_id JOIN payment p ON st.staff_id = p.staff_id GROUP BY s.store_id` | `SELECT s.store_id, SUM(p.amount) AS revenue FROM store s JOIN inventory i ON s.store_id = i.store_id JOIN rental r ON i.inventory_id = r.inventory_id JOIN payment p ON r.rental_id = p.rental_id GROUP BY s.store_id` | 0 | 0 | Both are valid interpretations of "revenue per store" — one joins via staff, the other via inventory. Revenue totals are close ($33,489 vs $33,679 for store 1) because 5 payments lack rental links. Binary sees both as equally wrong |

Scenarios 3, 5, and 6 all score **EM=0, EX=0** — yet they represent vastly different levels of correctness. One has the right answer with an extra column, another has exact values in the wrong order, and the last uses a different but reasonable query strategy. Binary metrics flatten all of this into the same "fail."

This is fine for leaderboards. But in the current AI landscape — where teams iterate on prompts, fine-tune models, and build agentic SQL pipelines — **binary scores are not enough**. When two models both score 65% EX, you can't tell which one is closer to being right on the remaining 35%.

As Pinna et al. (2025) note, binary metrics "fail to capture the similarities and differences between equivalent SQL queries" and "overlook critical aspects such as partial correctness, structural differences, and semantic equivalence" [[1]](#references).

## Our Approach

This suite replaces binary pass/fail with **continuous [0,1] scores** across four dimensions, then combines them into a single weighted Composite Score.

**The first step is intent-aware column selection.** Before computing any metric, the benchmark determines which columns actually matter for answering the user's question. An LLM (or heuristic fallback) examines the natural-language query, the generated SQL, and the expected SQL, then produces a column mapping — selecting only the relevant columns from both result sets and aligning them. This means extra columns like `category_id` in a "count movies by category" query are ignored, and evaluation focuses on the columns the user actually asked for.

Once the relevant columns are projected:

| Metric | What it measures |
|--------|-----------------|
| **S_C (Semantic Similarity)** | How similar the SQL code is structurally (cosine similarity of embeddings) |
| **S_T (Result Similarity)** | How close the projected query results are to the expected output (intent-aware edit distance) |
| **LLM Score** | An LLM-as-judge evaluation of overall query quality, with reasoning |
| **VES (Valid Efficiency Score)** | How fast the generated query runs relative to the reference |
| **Composite Score** | Weighted combination: `W1*S_T + W2*S_C + W3*LLM + W4*VES` |

With these metrics you can:

- **See incremental progress** — a prompt change that moves S_T from 0.4 to 0.8 is invisible to EX but clearly measurable here
- **Pinpoint failure modes** — low S_C but high S_T means the SQL looks different but produces correct results; the inverse means the SQL looks right but returns wrong data
- **Compare models meaningfully** — two models at the same EX can have very different partial-correctness profiles

### Benchmark Results for the Same 6 Examples

Here's what happens when we run the same queries through our benchmark:

**Models used:**
- Embedding model: `text-embedding-qwen3-embedding-8b` (for S_C)
- Column selection & LLM judge: `google/gemma-4-26b-a4b` (for intent-aware column mapping and LLM Score)

**Default weights:** W1(S_T)=0.3, W2(S_C)=0.2, W3(LLM)=0.3, W4(VES)=0.2

| # | User Question | EM | EX | S_C | S_T | LLM | VES | Composite | Insight |
|---|--------------|----|----|-----|-----|-----|-----|-----------|---------|
| 1 | *List all film titles* | 1 | 1 | 1.000 | 1.000 | 1.000 | 0.631 | 0.926 | Perfect across all dimensions |
| 2 | *Count films per category* | 0 | 1 | 0.986 | 1.000 | 1.000 | 0.768 | 0.951 | S_C=0.986 confirms the SQL is nearly identical; S_T=1.0 shows results match perfectly |
| 3 | *Count films per category* (extra col) | 0 | 0 | 0.978 | 1.000 | 1.000 | 0.707 | 0.937 | **EX=0 but Composite=0.937** — intent-aware column selection ignored `category_id`, so S_T=1.0 |
| 4 | *Top 10 customers by rental count* | 0 | 1 | 0.982 | 1.000 | 1.000 | 0.000 | 0.796 | Different GROUP BY but same results; VES=0 because EX comparison found row order mismatch |
| 5 | *Actors sorted by last name* | 0 | 0 | 0.956 | 1.000 | 1.000 | 0.000 | 0.791 | **EX=0 but S_T=1.0** — same values, reversed order. LLM=1.0 shows gemma is lenient on sort direction |
| 6 | *Revenue per store* | 0 | 0 | 0.828 | 0.000 | 0.000 | 0.000 | 0.166 | S_C=0.828 shows SQL is structurally related; S_T=0.0 confirms results actually differ (different join paths) |

Compare scenarios 3, 5, and 6 — all **EM=0, EX=0** under binary scoring, but our Composite scores are **0.937**, **0.791**, and **0.166** respectively. Now you can clearly see that #3 is essentially correct, #5 has the right data but wrong sort order, and #6 is genuinely different.

**Note on LLM judge sensitivity:** Example 5 scored LLM=0.0 with `qwen2.5-7b-instruct` (strict on sort order) but LLM=1.0 with `gemma-4-26b-a4b` (lenient). This highlights that LLM-as-judge scores are model-dependent — choose a judge model that matches your strictness requirements.

### Tuning Weights for Different Analysis Goals

The Composite Score is `W1*S_T + W2*S_C + W3*LLM + W4*VES`. Changing the weights shifts the focus of your analysis. Here's how the same 6 queries score under different weight profiles:

| # | Default (0.3, 0.2, 0.3, 0.2) | Result-focused (0.7, 0.1, 0.1, 0.1) | Semantic-focused (0.1, 0.7, 0.1, 0.1) | LLM-heavy (0.1, 0.1, 0.7, 0.1) |
|---|-------------------------------|--------------------------------------|----------------------------------------|----------------------------------|
| 1 | 0.926 | 0.963 | 0.963 | 0.963 |
| 2 | 0.951 | 0.975 | 0.967 | 0.975 |
| 3 | 0.937 | 0.968 | 0.955 | 0.968 |
| 4 | 0.796 | 0.898 | 0.887 | 0.898 |
| 5 | 0.791 | 0.896 | 0.869 | 0.896 |
| 6 | 0.166 | 0.083 | 0.580 | 0.083 |

**What the weight profiles reveal:**

- **Result-focused** (high W1) — prioritizes "did the query return the right data?" Best for evaluating answer correctness.
- **Semantic-focused** (high W2) — prioritizes "does the SQL look right structurally?" Example 6 jumps from 0.166 to 0.580 because the SQL is still somewhat similar (both are aggregations on store). Best for evaluating SQL generation quality independent of execution.
- **LLM-heavy** (high W3) — prioritizes the LLM judge's holistic assessment. With gemma-4-26b, this profile matches Result-focused because the judge agrees with S_T on all pass/fail cases. A stricter judge model would produce different scores here.
- **Default** — balanced view across all dimensions. Good general-purpose starting point.

Choose weights based on what matters most for your use case. Use the interactive Dashboard in the Excel report or the weight sliders in the HTML report to experiment in real time.

> Run it yourself: `python main.py --input data/readme_examples.json`

## Architecture

### Files

```
model.py               # Data models and utility helpers
config.py              # Runtime configuration and scoring weights
metric.py              # Metric calculators and intent-aware column selection
mock_database.py       # SQLite executor used for Sakila benchmarking
main.py                # CLI entry point, Excel export, and HTML report
report.py              # Interactive HTML report generator (Plotly.js)
generate_chart.py      # Standalone composite score chart (matplotlib)
requirements.txt       # Python dependencies
README.md              # This file
ARCHITECTURE.md        # Detailed architecture documentation
data/                  # Input benchmark cases (JSON)
results/               # Generated Excel and HTML reports
sakila.db              # SQLite database used for execution testing
```

### Metric Computation Flow

```
For each test case:
┌─────────────────────────────────────────────────────────────┐
│ 1. Execute both queries on sakila.db                        │
│    - Get generated and expected result sets with timing     │
├─────────────────────────────────────────────────────────────┤
│ 2. Intent-Aware Column Selection                            │
│    - Use LLM chat model, or fallback heuristics             │
│    - Select only columns required by the user query         │
│    - Project both result sets to relevant columns           │
├─────────────────────────────────────────────────────────────┤
│ 3. Execution Accuracy (EX) — internal                       │
│    - Compare projected result rows (used by VES)            │
├─────────────────────────────────────────────────────────────┤
│ 4. Semantic Similarity (S_C)                                │
│    - Get embeddings from LM Studio for both SQLs            │
│    - Compute cosine similarity → [0,1]                      │
├─────────────────────────────────────────────────────────────┤
│ 5. Result Similarity (S_T)                                  │
│    - Compare projected relevant result columns              │
│      - Find minimum edit distance to reference columns      │
│      - Optionally penalize row-order mismatch               │
│    - Aggregate across all columns → [0,1]                  │
├─────────────────────────────────────────────────────────────┤
│ 6. LLM Score                                                │
│    - LLM-as-judge evaluates generated SQL against expected  │
│    - Considers query intent, result correctness, style      │
│    - Returns score [0,1] with reasoning                     │
├─────────────────────────────────────────────────────────────┤
│ 7. Valid Efficiency Score (VES)                              │
│    - Measures execution speed relative to reference query   │
│    - Only credits speed when results are correct (EX=1)     │
├─────────────────────────────────────────────────────────────┤
│ 8. Composite Score                                          │
│    - Composite = W1*S_T + W2*S_C + W3*LLM + W4*VES         │
│    - Default: W1=0.3, W2=0.2, W3=0.3, W4=0.2              │
└─────────────────────────────────────────────────────────────┘
```

## Setup

### Prerequisites

- **Python 3.11+**
- **LM Studio** (or any OpenAI-compatible local inference server) — download from https://lmstudio.ai/ or use Ollama
- **sakila.db** present in the project root

### Installation

1. Clone or download the project
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start your inference server and load the required models. Defaults in `config.py`:
   ```python
   LM_STUDIO_API_URL = "http://127.0.0.1:11434/v1"
   EMBEDDING_MODEL = "text-embedding-qwen3-embedding-8b"
   COLUMN_SELECTION_MODEL = "qwen2.5-7b-instruct"
   LLM_JUDGE_MODEL = "qwen2.5-7b-instruct"
   ```

## Usage

### Basic Usage

```bash
# Run with default settings (data/readme_examples.json)
python main.py

# Use custom input file
python main.py --input data/my_test_cases.json

# Specify output file
python main.py --output results/my_report.xlsx

# Adjust component weights
python main.py --w1 0.3 --w2 0.2 --w3 0.3 --w4 0.2

# Enable strict row-order comparison for result similarity
python main.py --table-order-sensitive

# Add a soft penalty when rows are shuffled
python main.py --table-order-mismatch-weight 0.25

# Generate standalone composite score chart
python generate_chart.py --input data/readme_examples.json --output assets/composite_analysis.png
```

### Input Format

JSON array with test cases:

```json
[
  {
    "natural_language": "Count employees by department",
    "generated_sql": "SELECT department, COUNT(*) FROM employees GROUP BY dept",
    "expected_sql": "SELECT department, COUNT(*) FROM employees GROUP BY department"
  }
]
```

### Output

The tool generates both an **Excel** report (4 sheets: Summary, Results, Info, Dashboard with live weight recalculation) and an **interactive HTML** report with weight sliders, radar charts, heatmaps, and execution time comparisons.

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
LLM_JUDGE_MODEL = "..."
```

If you do not want LLM-based column selection:

```python
COLUMN_SELECTION_LLM_ENABLED = False
```

### Adding New Metrics

Add new functions to `metric.py` and update `run_benchmark()`:

```python
def calculate_custom_metric(gen_result, ref_result) -> float:
    # Your logic here
    return score
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Cannot connect to LM Studio | Ensure inference server is running on the URL in `config.py`. Suite continues with fallback (score = 0). |
| Column selection model not loaded | Falls back to common-column heuristics. Load the chat model for full intent-aware judging. |
| SQLite execution fails | Ensure `sakila.db` exists in project root and SQL is valid SQLite syntax. |
| Excel not created | Check write permissions and that `results/` directory exists. |

## License

MIT License

## References

1. Pinna, G., Perezhohin, Y., Manzoni, L., & Castelli, M. (2025). *Redefining text-to-SQL metrics by incorporating semantic and structural similarity.* Scientific Reports, 15. https://www.nature.com/articles/s41598-025-04890-9
2. Li, J., Hui, B., Qu, G., Yang, J., Li, B., Li, B., ... & others. (2024). *Can LLM already serve as a database interface? A big bench for large-scale database grounded text-to-SQLs.* Advances in Neural Information Processing Systems, 36. (BIRD Benchmark) https://bird-bench.github.io/
