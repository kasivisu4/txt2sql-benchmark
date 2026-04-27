"""Generate a weight-tuning comparison chart for the README examples.

Uses the pre-computed benchmark scores for the three intent-aware column
selection cases and plots how each composite score changes across four
weight profiles.  No LLM or database calls — scores are hard-coded from
the benchmark run.

Usage:
    python generate_weight_chart.py
    python generate_weight_chart.py --output assets/weight_tuning.png
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ── Pre-computed scores from benchmark run ────────────────────────────────────
# Columns: s_t, s_c, llm, ves
CASES = [
    {
        "label": "Case 1\nExtra actor_id",
        "short": "C1",
        "s_t": 0.964,
        "s_c": 0.977,
        "llm": 1.000,
        "ves": 0.000,
    },
    {
        "label": "Case 2\nExtra customer_id/email",
        "short": "C2",
        "s_t": 1.000,
        "s_c": 0.919,
        "llm": 1.000,
        "ves": 0.284,
    },
    {
        "label": "Case 3\nMissing rental_rate",
        "short": "C3",
        "s_t": 1.000,
        "s_c": 0.820,
        "llm": 0.500,
        "ves": 0.707,
    },
]

PROFILES = [
    {"name": "Default\n(0.3, 0.2, 0.3, 0.2)", "w": (0.3, 0.2, 0.3, 0.2)},
    {"name": "Result-focused\n(0.7, 0.1, 0.1, 0.1)", "w": (0.7, 0.1, 0.1, 0.1)},
    {"name": "Semantic-focused\n(0.1, 0.7, 0.1, 0.1)", "w": (0.1, 0.7, 0.1, 0.1)},
    {"name": "LLM-heavy\n(0.1, 0.1, 0.7, 0.1)", "w": (0.1, 0.1, 0.7, 0.1)},
]


def composite(case: dict, weights: tuple) -> float:
    w1, w2, w3, w4 = weights
    return w1 * case["s_t"] + w2 * case["s_c"] + w3 * case["llm"] + w4 * case["ves"]


def generate_weight_chart(output_file: str) -> None:
    n_profiles = len(PROFILES)
    n_cases = len(CASES)

    # Build score matrix [profile x case]
    scores = [
        [composite(c, p["w"]) for c in CASES]
        for p in PROFILES
    ]

    # ── Layout ────────────────────────────────────────────────────────────────
    fig, (ax_main, ax_delta) = plt.subplots(
        1, 2,
        figsize=(16, 6),
        gridspec_kw={"width_ratios": [3, 2]},
    )
    fig.patch.set_facecolor("#f9f9f9")
    for ax in (ax_main, ax_delta):
        ax.set_facecolor("#f9f9f9")

    COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]
    CASE_COLORS = ["#4C72B0", "#DD8452", "#55A868"]

    # ── Left: grouped bar chart ───────────────────────────────────────────────
    x = np.arange(n_cases)
    bar_w = 0.18
    offsets = np.linspace(-(n_profiles - 1) / 2, (n_profiles - 1) / 2, n_profiles) * bar_w

    for pi, (profile, offset) in enumerate(zip(PROFILES, offsets)):
        vals = scores[pi]
        bars = ax_main.bar(
            x + offset, vals, bar_w,
            label=profile["name"].replace("\n", " "),
            color=COLORS[pi],
            alpha=0.88,
            edgecolor="white",
            linewidth=0.6,
        )
        for bar, val in zip(bars, vals):
            ax_main.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.008,
                f"{val:.3f}",
                ha="center", va="bottom",
                fontsize=7.5, color="#333333",
            )

    ax_main.set_title(
        "Composite Score by Weight Profile",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax_main.set_ylabel("Composite Score", fontsize=10)
    ax_main.set_xticks(x)
    ax_main.set_xticklabels([c["label"] for c in CASES], fontsize=9)
    ax_main.set_ylim(0.0, 1.08)
    ax_main.axhline(1.0, color="#aaaaaa", linewidth=0.8, linestyle="--")
    ax_main.grid(True, axis="y", linestyle="--", alpha=0.4, color="#cccccc")
    ax_main.spines[["top", "right"]].set_visible(False)
    ax_main.legend(
        fontsize=8, loc="lower right",
        framealpha=0.7, edgecolor="#cccccc",
    )

    # ── Right: delta heatmap (score − default) ────────────────────────────────
    default_scores = scores[0]
    delta = np.array([
        [scores[pi][ci] - default_scores[ci] for ci in range(n_cases)]
        for pi in range(1, n_profiles)   # skip Default row
    ])

    vmax = max(abs(delta.min()), abs(delta.max()), 0.05)
    im = ax_delta.imshow(delta, cmap="RdYlGn", vmin=-vmax, vmax=vmax, aspect="auto")

    for pi in range(delta.shape[0]):
        for ci in range(delta.shape[1]):
            val = delta[pi, ci]
            ax_delta.text(
                ci, pi, f"{val:+.3f}",
                ha="center", va="center",
                fontsize=10, fontweight="bold",
                color="black",
            )

    ax_delta.set_title(
        "Score Δ vs Default Weights",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax_delta.set_xticks(range(n_cases))
    ax_delta.set_xticklabels([c["short"] for c in CASES], fontsize=10)
    ax_delta.set_yticks(range(len(PROFILES) - 1))
    ax_delta.set_yticklabels(
        [p["name"].replace("\n", " ") for p in PROFILES[1:]],
        fontsize=8.5,
    )
    ax_delta.spines[["top", "right", "bottom", "left"]].set_visible(False)
    ax_delta.tick_params(length=0)

    cb = fig.colorbar(im, ax=ax_delta, fraction=0.046, pad=0.04)
    cb.set_label("Δ Composite", fontsize=9)
    cb.ax.tick_params(labelsize=8)

    fig.tight_layout(pad=2.5)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_file, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Chart saved to: {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate weight-tuning comparison chart")
    parser.add_argument(
        "--output",
        default="assets/weight_tuning.png",
        help="Output PNG file path",
    )
    args = parser.parse_args()
    generate_weight_chart(args.output)
