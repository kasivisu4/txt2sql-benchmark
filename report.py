"""Generate an interactive HTML analysis report from benchmark results.

Creates a standalone HTML file with Plotly.js charts and interactive weight
sliders. No server required — just open the file in a browser.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List

from model import MetricResult


def generate_html_report(
    results: List[MetricResult],
    summary_stats: dict,
    output_file: str,
) -> None:
    """Write a self-contained interactive HTML report.

    Charts included:
    1. Interactive weight sliders with live composite recalculation
    2. Radar chart — per-query component profile
    3. Heatmap — all queries × all metrics
    4. Weakness analysis — lowest component per query highlighted
    5. Metric distributions — box plots across all queries
    6. Scatter: S_T vs S_C coloured by composite
    7. Execution time comparison (gen vs ref)
    """
    # Prepare data as JSON for embedding
    queries = []
    for i, r in enumerate(results):
        queries.append(
            {
                "id": i + 1,
                "label": f"Q{i + 1}",
                "nl": r.test_case.natural_language,
                "gen_sql": r.test_case.generated_sql,
                "exp_sql": r.test_case.expected_sql,
                "s_t": round(r.table_sim, 4),
                "s_c": round(r.semantic_sim, 4),
                "llm": round(r.llm_score, 4),
                "llm_reasoning": r.llm_reasoning,
                "ves": round(r.ves, 4),
                "composite": round(r.composite_score, 4),
                "exec_gen_ms": round(r.execution_time_gen_ms, 1),
                "exec_ref_ms": round(r.execution_time_ref_ms, 1),
                "cols_gen": r.selected_generated_columns,
                "cols_exp": r.selected_expected_columns,
                "col_source": r.column_selection_source,
                "col_confidence": round(r.column_selection_confidence, 2),
            }
        )

    data_json = json.dumps(queries)
    weights_json = json.dumps(
        {
            "w1": summary_stats["w1"],
            "w2": summary_stats["w2"],
            "w3": summary_stats["w3"],
            "w4": summary_stats["w4"],
        }
    )
    stats_json = json.dumps(
        {
            "total_tests": summary_stats["total_tests"],
            "avg_s_c": round(summary_stats["avg_semantic_sim"], 4),
            "avg_s_t": round(summary_stats["avg_table_sim"], 4),
            "avg_llm": round(summary_stats["avg_llm_score"], 4),
            "avg_ves": round(summary_stats["avg_ves"], 4),
            "avg_composite": round(summary_stats["avg_composite_score"], 4),
            "total_time_ms": round(summary_stats["total_time_ms"], 1),
        }
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = _build_html(data_json, weights_json, stats_json, generated_at)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(output_file).write_text(html, encoding="utf-8")


def _build_html(
    data_json: str,
    weights_json: str,
    stats_json: str,
    generated_at: str,
) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>txt2sql Benchmark — Analysis Report</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2d3040;
    --text: #e0e0e0; --muted: #9098a9; --accent: #6c8cff;
    --green: #4ade80; --yellow: #facc15; --red: #f87171;
    --blue: #60a5fa; --purple: #a78bfa; --orange: #fb923c;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.5;
  }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 1.8rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 24px; }}

  /* ── KPI cards ─────────────────────────────────────────── */
  .kpi-row {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin-bottom: 28px;
  }}
  .kpi {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px; text-align: center;
  }}
  .kpi .value {{ font-size: 1.6rem; font-weight: 700; }}
  .kpi .label {{ font-size: 0.78rem; color: var(--muted); margin-top: 2px; }}

  /* ── Sections ──────────────────────────────────────────── */
  .section {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px; margin-bottom: 20px;
  }}
  .section h2 {{ font-size: 1.15rem; margin-bottom: 14px; }}
  .section p.desc {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 14px; }}

  /* ── Weight sliders ────────────────────────────────────── */
  .slider-grid {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px 28px;
  }}
  .slider-item {{ display: flex; align-items: center; gap: 10px; }}
  .slider-item label {{ min-width: 120px; font-size: 0.88rem; }}
  .slider-item input[type=range] {{ flex: 1; accent-color: var(--accent); }}
  .slider-item .val {{ min-width: 40px; text-align: right; font-weight: 600; }}
  #weightSum {{ font-weight: 700; }}

  /* ── Per-query detail table ────────────────────────────── */
  table.detail {{
    width: 100%; border-collapse: collapse; font-size: 0.82rem;
  }}
  table.detail th {{
    background: var(--border); color: var(--text);
    padding: 8px 10px; text-align: center;
  }}
  table.detail td {{
    padding: 7px 10px; text-align: center; border-bottom: 1px solid var(--border);
  }}
  table.detail tr:hover {{ background: rgba(108,140,255,0.07); }}
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 6px;
    font-size: 0.75rem; font-weight: 600;
  }}
  .badge-pass {{ background: rgba(74,222,128,0.15); color: var(--green); }}
  .badge-fail {{ background: rgba(248,113,113,0.15); color: var(--red); }}
  .weakness {{ color: var(--red); font-weight: 700; }}

  /* ── Chart row ─────────────────────────────────────────── */
  .chart-row {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
  }}
  @media (max-width: 900px) {{
    .chart-row {{ grid-template-columns: 1fr; }}
    .slider-grid {{ grid-template-columns: 1fr; }}
  }}

  /* ── SQL detail popup ──────────────────────────────────── */
  .sql-detail {{ display: none; margin-top: 8px; }}
  .sql-detail.open {{ display: block; }}
  .sql-detail pre {{
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px; font-size: 0.78rem;
    overflow-x: auto; white-space: pre-wrap;
  }}
  .toggle-btn {{
    cursor: pointer; color: var(--accent); text-decoration: underline;
    font-size: 0.8rem; border: none; background: none;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>txt2sql Benchmark — Analysis Report</h1>
  <div class="subtitle">Generated {generated_at}</div>

  <!-- KPI cards (filled by JS) -->
  <div class="kpi-row" id="kpiRow"></div>

  <!-- 1. Weight sliders -->
  <div class="section">
    <h2>Interactive Weight Tuning</h2>
    <p class="desc">Drag the sliders to change weights. All charts, the table, and KPIs update live.
       Sum shown below — keep it at 1.0 for a normalised score.</p>
    <div class="slider-grid" id="sliders"></div>
    <div style="margin-top:10px; color:var(--muted); font-size:0.85rem;">
      Sum = <span id="weightSum">1.00</span>
    </div>
  </div>

  <!-- 2. Composite breakdown (stacked bar + radar) -->
  <div class="section">
    <h2>Composite Score Breakdown</h2>
    <p class="desc">Stacked bars show each component's weighted contribution.
       The radar chart reveals the shape — a perfect query fills the entire polygon.</p>
    <div class="chart-row">
      <div id="chartStacked"></div>
      <div id="chartRadar"></div>
    </div>
  </div>

  <!-- 3. Heatmap -->
  <div class="section">
    <h2>Metric Heatmap</h2>
    <p class="desc">Raw (unweighted) scores for every query × metric.
       Darker red = lower score. Instantly spot which queries and dimensions need attention.</p>
    <div id="chartHeatmap"></div>
  </div>

  <!-- 4. Distributions + Scatter -->
  <div class="section">
    <h2>Score Distributions &amp; Correlations</h2>
    <p class="desc">Box plots show the spread of each metric across queries.
       The scatter plot reveals how Table Similarity and Semantic Similarity relate —
       marker size encodes the composite score.</p>
    <div class="chart-row">
      <div id="chartBox"></div>
      <div id="chartScatter"></div>
    </div>
  </div>

  <!-- 5. Execution time -->
  <div class="section">
    <h2>Execution Time Analysis</h2>
    <p class="desc">Generated vs Reference query execution time.
       Large gaps signal efficiency issues (captured by VES).</p>
    <div id="chartExecTime"></div>
  </div>

  <!-- 6. Detail table -->
  <div class="section">
    <h2>Per-Query Detail</h2>
    <p class="desc">Click a row's ⊕ to expand SQL and column details.
       The weakest component in each row is highlighted in red.</p>
    <div style="overflow-x:auto;">
      <table class="detail" id="detailTable"></table>
    </div>
  </div>
</div>

<script>
// ── Embedded data ──────────────────────────────────────────────────────────
const DATA   = {data_json};
const W_INIT = {weights_json};
const STATS  = {stats_json};

// ── State ──────────────────────────────────────────────────────────────────
let W = {{ ...W_INIT }};

const COMPONENTS = [
  {{ key: 'w1', metric: 's_t', label: 'W1 · Table Sim (S_T)',    color: '#60a5fa' }},
  {{ key: 'w2', metric: 's_c', label: 'W2 · Semantic Sim (S_C)', color: '#a78bfa' }},
  {{ key: 'w3', metric: 'llm', label: 'W3 · LLM Score',          color: '#4ade80' }},
  {{ key: 'w4', metric: 'ves', label: 'W4 · VES',                color: '#fb923c' }},
];

const plotBg   = '#1a1d27';
const plotGrid = '#2d3040';
const plotText = '#9098a9';
const plotLayout = {{
  paper_bgcolor: plotBg, plot_bgcolor: plotBg, font: {{ color: plotText, size: 11 }},
  margin: {{ l: 50, r: 20, t: 40, b: 50 }},
  xaxis: {{ gridcolor: plotGrid }}, yaxis: {{ gridcolor: plotGrid }},
}};

// ── Helpers ────────────────────────────────────────────────────────────────
function composite(q) {{
  return W.w1 * q.s_t + W.w2 * q.s_c + W.w3 * q.llm + W.w4 * q.ves;
}}
function avg(arr) {{ return arr.reduce((a, b) => a + b, 0) / (arr.length || 1); }}
function fmt(v) {{ return v.toFixed(4); }}

// ── KPI cards ──────────────────────────────────────────────────────────────
function renderKPIs() {{
  const composites = DATA.map(composite);
  const avgComp = avg(composites);
  const perfect = composites.filter(c => c >= 0.9999).length;
  const weak    = composites.filter(c => c < 0.7).length;
  const cards = [
    {{ value: DATA.length, label: 'Total Queries' }},
    {{ value: fmt(avgComp), label: 'Avg Composite' }},
    {{ value: fmt(avg(DATA.map(q => q.s_t))), label: 'Avg S_T' }},
    {{ value: fmt(avg(DATA.map(q => q.s_c))), label: 'Avg S_C' }},
    {{ value: fmt(avg(DATA.map(q => q.llm))), label: 'Avg LLM' }},
    {{ value: fmt(avg(DATA.map(q => q.ves))), label: 'Avg VES' }},
    {{ value: perfect, label: 'Perfect (1.0)' }},
    {{ value: weak, label: 'Weak (< 0.7)' }},
  ];
  document.getElementById('kpiRow').innerHTML = cards.map(c =>
    `<div class="kpi"><div class="value">${{c.value}}</div><div class="label">${{c.label}}</div></div>`
  ).join('');
}}

// ── Sliders ────────────────────────────────────────────────────────────────
function renderSliders() {{
  const el = document.getElementById('sliders');
  el.innerHTML = COMPONENTS.map(c => `
    <div class="slider-item">
      <label>${{c.label}}</label>
      <input type="range" min="0" max="100" value="${{Math.round(W[c.key]*100)}}"
             data-key="${{c.key}}" oninput="onSlider(this)">
      <span class="val" id="val_${{c.key}}">${{W[c.key].toFixed(2)}}</span>
    </div>
  `).join('');
  updateSum();
}}
function onSlider(el) {{
  const key = el.dataset.key;
  W[key] = el.value / 100;
  document.getElementById('val_' + key).textContent = W[key].toFixed(2);
  updateSum();
  refreshAll();
}}
function updateSum() {{
  const s = W.w1 + W.w2 + W.w3 + W.w4;
  const el = document.getElementById('weightSum');
  el.textContent = s.toFixed(2);
  el.style.color = Math.abs(s - 1) < 0.005 ? '#4ade80' : '#f87171';
}}

// ── Charts ─────────────────────────────────────────────────────────────────
function plotStacked() {{
  const labels = DATA.map(q => q.label);
  const traces = COMPONENTS.map(c => ({{
    name: c.label,
    x: labels,
    y: DATA.map(q => W[c.key] * q[c.metric]),
    type: 'bar',
    marker: {{ color: c.color }},
    hovertemplate: '%{{x}}: %{{y:.4f}}<extra>' + c.label + '</extra>',
  }}));
  // Composite line overlay
  traces.push({{
    name: 'Composite',
    x: labels,
    y: DATA.map(composite),
    type: 'scatter', mode: 'markers+lines',
    marker: {{ color: '#facc15', size: 8, symbol: 'diamond' }},
    line: {{ dash: 'dot', width: 2 }},
    yaxis: 'y',
  }});
  Plotly.react('chartStacked', traces, {{
    ...plotLayout,
    barmode: 'stack',
    title: 'Weighted Component Contributions',
    yaxis: {{ ...plotLayout.yaxis, title: 'Score', range: [0, 1.15] }},
    legend: {{ orientation: 'h', y: -0.18 }},
    height: 380,
  }}, {{ responsive: true }});
}}

function plotRadar() {{
  const cats = ['S_T', 'S_C', 'LLM', 'VES', 'S_T'];  // close the polygon
  const traces = DATA.map(q => ({{
    type: 'scatterpolar',
    r: [q.s_t, q.s_c, q.llm, q.ves, q.s_t],
    theta: cats,
    fill: 'toself',
    name: q.label + ' — ' + q.nl.slice(0, 40),
    opacity: 0.35,
  }}));
  Plotly.react('chartRadar', traces, {{
    ...plotLayout,
    polar: {{
      bgcolor: plotBg,
      radialaxis: {{ visible: true, range: [0, 1], gridcolor: plotGrid, color: plotText }},
      angularaxis: {{ gridcolor: plotGrid, color: plotText }},
    }},
    title: 'Query Profiles (raw scores)',
    showlegend: true,
    legend: {{ font: {{ size: 9 }}, y: -0.15, orientation: 'h' }},
    height: 380,
  }}, {{ responsive: true }});
}}

function plotHeatmap() {{
  const metrics = ['S_T', 'S_C', 'LLM', 'VES', 'Composite'];
  const z = DATA.map(q => [q.s_t, q.s_c, q.llm, q.ves, composite(q)]);
  const labels = DATA.map(q => q.label);
  // Annotate each cell
  const annotations = [];
  z.forEach((row, i) => row.forEach((val, j) => {{
    annotations.push({{
      x: metrics[j], y: labels[i],
      text: val.toFixed(3), showarrow: false,
      font: {{ color: val < 0.5 ? '#fff' : '#000', size: 11 }},
    }});
  }}));
  Plotly.react('chartHeatmap', [{{
    z, x: metrics, y: labels,
    type: 'heatmap',
    colorscale: [[0, '#991b1b'], [0.5, '#facc15'], [1, '#16a34a']],
    zmin: 0, zmax: 1,
    hovertemplate: '%{{y}}, %{{x}}: %{{z:.4f}}<extra></extra>',
  }}], {{
    ...plotLayout,
    annotations,
    title: 'Score Heatmap (raw scores + live composite)',
    height: 60 + DATA.length * 45,
    yaxis: {{ autorange: 'reversed', color: plotText }},
    xaxis: {{ side: 'top', color: plotText }},
  }}, {{ responsive: true }});
}}

function plotBox() {{
  const metrics = [
    {{ key: 's_t', name: 'S_T', color: '#60a5fa' }},
    {{ key: 's_c', name: 'S_C', color: '#a78bfa' }},
    {{ key: 'llm', name: 'LLM', color: '#4ade80' }},
    {{ key: 'ves', name: 'VES', color: '#fb923c' }},
  ];
  const traces = metrics.map(m => ({{
    y: DATA.map(q => q[m.key]),
    name: m.name, type: 'box',
    marker: {{ color: m.color }},
    boxpoints: 'all', jitter: 0.4, pointpos: -1.5,
  }}));
  Plotly.react('chartBox', traces, {{
    ...plotLayout,
    title: 'Metric Distributions',
    yaxis: {{ ...plotLayout.yaxis, title: 'Score', range: [0, 1.08] }},
    showlegend: false,
    height: 360,
  }}, {{ responsive: true }});
}}

function plotScatter() {{
  const composites = DATA.map(composite);
  Plotly.react('chartScatter', [{{
    x: DATA.map(q => q.s_t),
    y: DATA.map(q => q.s_c),
    mode: 'markers+text',
    text: DATA.map(q => q.label),
    textposition: 'top center',
    textfont: {{ color: plotText, size: 10 }},
    marker: {{
      size: composites.map(c => 12 + c * 22),
      color: composites,
      colorscale: [[0, '#f87171'], [0.5, '#facc15'], [1, '#4ade80']],
      cmin: 0, cmax: 1,
      colorbar: {{ title: 'Composite', tickfont: {{ color: plotText }} }},
      line: {{ width: 1, color: '#fff' }},
    }},
    hovertemplate: '%{{text}}<br>S_T=%{{x:.3f}}<br>S_C=%{{y:.3f}}<br>Comp=%{{marker.color:.3f}}<extra></extra>',
  }}], {{
    ...plotLayout,
    title: 'S_T vs S_C (size & colour = Composite)',
    xaxis: {{ ...plotLayout.xaxis, title: 'Table Similarity (S_T)', range: [-0.05, 1.1] }},
    yaxis: {{ ...plotLayout.yaxis, title: 'Semantic Similarity (S_C)', range: [-0.05, 1.1] }},
    height: 360,
  }}, {{ responsive: true }});
}}

function plotExecTime() {{
  Plotly.react('chartExecTime', [
    {{
      x: DATA.map(q => q.label), y: DATA.map(q => q.exec_gen_ms),
      name: 'Generated', type: 'bar',
      marker: {{ color: '#60a5fa' }},
      hovertemplate: '%{{x}}: %{{y:.1f}} ms<extra>Generated</extra>',
    }},
    {{
      x: DATA.map(q => q.label), y: DATA.map(q => q.exec_ref_ms),
      name: 'Reference', type: 'bar',
      marker: {{ color: '#4ade80' }},
      hovertemplate: '%{{x}}: %{{y:.1f}} ms<extra>Reference</extra>',
    }},
  ], {{
    ...plotLayout,
    barmode: 'group',
    title: 'Execution Time: Generated vs Reference',
    yaxis: {{ ...plotLayout.yaxis, title: 'Time (ms)' }},
    legend: {{ orientation: 'h', y: -0.18 }},
    height: 320,
  }}, {{ responsive: true }});
}}

// ── Detail table ───────────────────────────────────────────────────────────
function renderTable() {{
  const metricKeys = ['s_t', 's_c', 'llm', 'ves'];
  let html = `<thead><tr>
    <th></th><th>#</th><th>Query</th>
    <th>S_T</th><th>S_C</th><th>LLM</th><th>VES</th>
    <th>Composite</th><th>Weakness</th>
  </tr></thead><tbody>`;

  DATA.forEach((q, i) => {{
    const comp = composite(q);
    const vals = {{ s_t: q.s_t, s_c: q.s_c, llm: q.llm, ves: q.ves }};
    const minKey = Object.entries(vals).sort((a, b) => a[1] - b[1])[0];
    const weakLabel = {{ s_t: 'S_T', s_c: 'S_C', llm: 'LLM', ves: 'VES' }}[minKey[0]];

    html += `<tr>
      <td><button class="toggle-btn" onclick="toggleDetail(${{i}})">⊕</button></td>
      <td>${{q.label}}</td>
      <td style="text-align:left; max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
          title="${{q.nl}}">${{q.nl}}</td>`;
    metricKeys.forEach(k => {{
      const cls = k === minKey[0] ? ' class="weakness"' : '';
      html += `<td${{cls}}>${{q[k].toFixed(4)}}</td>`;
    }});
    html += `<td style="font-weight:700">${{comp.toFixed(4)}}</td>
      <td class="weakness">${{weakLabel}} (${{minKey[1].toFixed(2)}})</td>
    </tr>
    <tr><td colspan="9">
      <div class="sql-detail" id="detail_${{i}}">
        <pre><b>Natural Language:</b> ${{q.nl}}

<b>Generated SQL:</b>
${{q.gen_sql}}

<b>Expected SQL:</b>
${{q.exp_sql}}

<b>LLM Judge Reasoning:</b> ${{q.llm_reasoning}}

<b>Evaluated Columns (gen):</b> ${{q.cols_gen.join(', ')}}
<b>Evaluated Columns (exp):</b> ${{q.cols_exp.join(', ')}}
<b>Column Selection:</b> ${{q.col_source}} (confidence: ${{q.col_confidence}})
<b>Exec Times:</b> gen=${{q.exec_gen_ms}}ms  ref=${{q.exec_ref_ms}}ms</pre>
      </div>
    </td></tr>`;
  }});
  html += '</tbody>';
  document.getElementById('detailTable').innerHTML = html;
}}

function toggleDetail(i) {{
  document.getElementById('detail_' + i).classList.toggle('open');
}}

// ── Orchestration ──────────────────────────────────────────────────────────
function refreshAll() {{
  renderKPIs();
  plotStacked();
  plotHeatmap();
  plotScatter();
  renderTable();
  // radar and box don't depend on weights
}}

// Initial render
renderKPIs();
renderSliders();
plotStacked();
plotRadar();
plotHeatmap();
plotBox();
plotScatter();
plotExecTime();
renderTable();
</script>
</body>
</html>"""
