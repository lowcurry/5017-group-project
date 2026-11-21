"""Render the archived study measures with Matplotlib."""
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator, PercentFormatter, ScalarFormatter


BLUE, TEAL, RED = '#345c95', '#087c70', '#a63a34'
SLATE, GOLD = '#64778b', '#d6a448'
INK, GRID = '#192b3c', '#dce5ec'
SETTINGS = {
    'font.family': 'DejaVu Sans', 'font.size': 10, 'text.color': INK,
    'axes.labelcolor': INK, 'axes.titlecolor': INK, 'axes.spines.top': False,
    'axes.spines.right': False, 'axes.spines.left': False, 'axes.edgecolor': GRID,
    'xtick.color': INK, 'ytick.color': INK, 'axes.axisbelow': True,
    'legend.frameon': False, 'svg.hashsalt': 'curve-3pool-effect-metrics-v2',
}

CHART_TITLES = {
    'coverage.svg': 'Raw, reproducible and published correct coverage by cohort',
    'paired-effects.svg': 'Evidence-guided minus ordinary prompting for each paired repetition',
    'verifier-faults.svg': 'PASS, FAIL and UNKNOWN outcomes of the frozen verifier checks',
    'arithmetic-faults.svg': 'Publication outcomes of injected shared arithmetic faults',
    'research-decisions.svg': 'Historical research actions and review referrals before and after verification',
    'quote-errors.svg': 'Cumulative distribution of historical quote errors by event',
    'efficiency.svg': 'Observed model token use and tokens per correct result',
}


def group_bars(ax, labels, series, *, cap=None, percent=False, fmt=None):
    """Draw a grouped comparison and explicitly label every value, including zero."""
    bar_width = 0.33
    for offset, (name, values, color, annotations) in zip((-0.5, 0.5), series):
        positions = [i + offset * bar_width for i in range(len(labels))]
        bars = ax.bar(positions, values, width=bar_width, label=name, color=color)
        if annotations is None:
            annotations = [format(value, fmt or ',.0f') for value in values]
        ax.bar_label(bars, labels=annotations, padding=4, fontsize=8.5, color=INK)
    ax.set_xticks(range(len(labels)), labels)
    ax.grid(axis='y', color=GRID, linewidth=0.8)
    if cap:
        ax.set_ylim(0, cap)
    if percent:
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.yaxis.set_major_formatter(PercentFormatter(100))


def counts_label(rows):
    return [f"{row['numerator']}/{row['denominator']}" for row in rows]


def coverage(find, data):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.1), sharey=True, layout='constrained')
    keys = ('correct', 'reproducible_correct', 'published_correct')
    for ax, cohort, pairs in zip(axes, ('scheduled', 'complete_pairs'),
                                 ('scheduled_pairs', 'completed_pairs')):
        series = []
        for group, label, color in (('G', 'G: ordinary', BLUE), ('E', 'E: evidence-guided', TEAL)):
            rows = [find('formal', cohort, 'overall', group, key) for key in keys]
            series.append((label, [row['value'] for row in rows], color, counts_label(rows)))
        ax.set_title(f"{cohort.replace('_', ' ').title()} · {data['pair_counts'][pairs]} pairs")
        group_bars(ax, ('Raw correct', 'Correct and\nreproducible', 'Correct and\npublished'),
                   series, cap=112, percent=True)
    axes[0].set_ylabel('Share of answerable components')
    axes[0].legend(loc='upper left', bbox_to_anchor=(0, 1.01), fontsize=8, ncols=2)
    fig.suptitle('Correct coverage at each stage', fontsize=16, weight='bold')
    return fig


def paired_effect(data):
    fig, ax = plt.subplots(figsize=(9.4, 5.8), layout='constrained')
    pairs = list(reversed(data['pairs']))
    ax.axvline(0, color=SLATE, linewidth=1)
    labels = []
    for y, pair in enumerate(pairs):
        g, e = (pair['groups'][group]['reproducible_correct'] for group in ('G', 'E'))
        delta = e - g
        color = TEAL if delta > 0 else RED if delta < 0 else SLATE
        ax.hlines(y, min(0, delta), max(0, delta), color=color, alpha=0.7)
        ax.scatter(delta, y, color=color, marker='o' if pair['completed'] else 'D', s=35)
        labels.append(f"{pair['task_id']}.{pair['repetition']}")
    ax.set_yticks(range(len(pairs)), labels, fontsize=8)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlim(ax.get_xlim()[0] - 0.25, ax.get_xlim()[1] + 0.25)
    ax.grid(axis='x', color=GRID)
    ax.set_xlabel('E minus G · correct and reproducible components')
    ax.set_ylabel('Task and repetition')
    ax.legend(handles=[
        Line2D([], [], color=SLATE, linestyle='none', marker='o', label='Both completed'),
        Line2D([], [], color=SLATE, linestyle='none', marker='D', label='Budget stop in pair'),
    ], loc='lower right')
    fig.suptitle('Paired effects across the 18 scheduled comparisons', fontsize=16, weight='bold')
    return fig


def stacked(ax, labels, groups, statuses, colors, lookup, xlabel, *, annotation_floor=3):
    labels = [f'{name}\n{group}' for name in labels for group in groups]
    left = [0] * len(labels)
    for status, color in zip(statuses, colors):
        counts = lookup(status)
        bars = ax.barh(range(len(labels)), counts, left=left, height=0.72,
                       label=status.replace('_', ' ').title(), color=color)
        ax.bar_label(bars, labels=[str(n) if n >= annotation_floor else '' for n in counts],
                     label_type='center', fontsize=9, color='white' if color != GOLD else INK)
        left = [start + count for start, count in zip(left, counts)]
    ax.set_yticks(range(len(labels)), labels)
    ax.invert_yaxis()
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel(xlabel)
    ax.grid(axis='x', color=GRID)
    ax.legend(loc='upper right', ncols=3, bbox_to_anchor=(1, 1.13))


def verifier_faults(find):
    fig, ax = plt.subplots(figsize=(9.4, 6.1), layout='constrained')
    buckets = (('Injected fault', 'fault'), ('Clean control', 'clean'),
               ('Legitimate refusal', 'legal_refusal'), ('Source unavailable', 'source_unavailable'),
               ('Source conflict', 'source_conflict'))
    layers = ('V1', 'V2')
    stacked(ax, [label for label, _ in buckets], layers, ('pass', 'fail', 'unknown'),
            (TEAL, RED, GOLD), lambda verdict: [
                find('verifier_faults', kind, 'overall', layer, verdict)['numerator']
                for _, kind in buckets for layer in layers
            ], 'Number of cases per verifier', annotation_floor=1)
    fig.suptitle('Frozen verifier fault scenarios', fontsize=16, weight='bold')
    return fig


def arithmetic_faults(find):
    fig, ax = plt.subplots(figsize=(9.4, 4.0), layout='constrained')
    buckets = ('clean', 'faulty_report', 'shared_verifier_bug')
    series = []
    for stage, color in (('before', BLUE), ('after', TEAL)):
        rows = [find('arithmetic_faults', kind, 'overall', stage, 'published') for kind in buckets]
        series.append((stage.title() + ' independent check', [row['value'] for row in rows],
                       color, counts_label(rows)))
    group_bars(ax, ('Correct controls', 'Wrong reports', 'Wrong reports with\nshared verifier bug'),
               series, cap=116, percent=True)
    ax.set_ylabel('Share of test cases published')
    ax.legend(loc='upper center', ncols=2, bbox_to_anchor=(0.5, 1.06))
    fig.suptitle('Independent arithmetic check: publication outcomes', fontsize=16, weight='bold')
    return fig


def research_decisions(find):
    fig, ax = plt.subplots(figsize=(9.4, 3.5), layout='constrained')
    stacked(ax, ['36 opportunities'], ('Raw', 'Verified'), ('correct', 'wrong', 'deferred'),
            (TEAL, RED, SLATE), lambda verdict: [
                find('research_decisions', 'scheduled', 'overall', stage, verdict)['numerator']
                for stage in ('raw', 'verified')
            ], 'Number of research decisions', annotation_floor=1)
    fig.suptitle('Historical decisions and manual review referrals', fontsize=16, weight='bold')
    return fig


def quote_errors(data):
    fig, ax = plt.subplots(figsize=(9.4, 4.3), layout='constrained')
    observations = defaultdict(list)
    for point in data['quote_points']:
        observations[point['event_id']].append(abs(float(point['extrapolation_error_usdt'])))
    upper = max(max(values) for values in observations.values())
    right = max(upper * 1.13, 0.012)
    for (event, name), color in zip((('ftx_2022', 'FTX Nov 2022'), ('usdc_2023', 'USDC Mar 2023')),
                                    (BLUE, TEAL)):
        values = sorted(observations[event])
        levels = [100 * i / len(values) for i in range(len(values) + 1)]
        ax.step([0, *values, right], [*levels, 100], where='post',
                color=color, linewidth=2, label=f'{name} · {len(values)} hourly targets')
    ax.set_xscale('symlog', linthresh=0.01)
    ticks = [value for value in (0, 0.01, 0.1, 1, 10) if value < right]
    ax.set_xticks(ticks, [f'{value:g}' for value in ticks])
    ax.set_xlim(0, right)
    ax.set_ylim(0, 105)
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.grid(color=GRID)
    ax.set_xlabel('Absolute difference (USDT) · logarithmic above 0.01')
    ax.set_ylabel('Share of historical targets at or below error')
    ax.legend(loc='lower right')
    fig.suptitle('Historical quote extrapolation error', fontsize=16, weight='bold')
    return fig


def efficiency(find):
    fig, axes = plt.subplots(1, 2, figsize=(10.3, 4.0), layout='constrained')
    panels = (
        ('Recorded tokens', ('All model\ntokens',), ('model_tokens',), 155000),
        ('Tokens per correct component', ('Reproducible', 'Published'),
         ('tokens_per_reproducible_correct', 'tokens_per_published_correct'), 4050),
    )
    for ax, (title, labels, metrics, cap) in zip(axes, panels):
        series = [(group, [find('formal', 'scheduled', 'overall', group, name)['value']
                           for name in metrics], color, None)
                  for group, color in (('G', BLUE), ('E', TEAL))]
        group_bars(ax, labels, series, cap=cap)
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.set_title(title)
    axes[0].legend(loc='upper left', ncols=2)
    fig.suptitle('Model usage and correct coverage yield', fontsize=16, weight='bold')
    return fig


def render(data, output):
    lookup = {(row['experiment'], row['cohort'], row['scope'], row['group'], row['metric']): row
              for row in data['metrics']}

    def find(*key):
        return lookup[key]

    recipes = (lambda: coverage(find, data), lambda: paired_effect(data),
               lambda: verifier_faults(find), lambda: arithmetic_faults(find),
               lambda: research_decisions(find), lambda: quote_errors(data), lambda: efficiency(find))
    with plt.rc_context(SETTINGS):
        for filename, draw in zip(CHART_TITLES, recipes, strict=True):
            figure = draw()
            try:
                figure.savefig(Path(output) / filename, format='svg',
                               metadata={'Title': CHART_TITLES[filename], 'Date': None})
            finally:
                plt.close(figure)
