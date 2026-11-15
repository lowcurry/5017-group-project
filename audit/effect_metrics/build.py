"""Export effect metrics, shareable SVG charts and a browser report from archived results."""
from argparse import ArgumentParser
import csv
from html import escape
import json
from pathlib import Path

from metrics import measure


def browser_report(charts, metrics):
    sections = '\n'.join(f'<figure><img src="{name}" alt="{escape(title)}">'
                         f'<figcaption>{escape(title)}</figcaption></figure>'
                         for name, title in charts.items())
    labels = ('Experiment', 'Cohort', 'Scope', 'Group or stage', 'Metric', 'Count',
              'Denominator', 'Value', 'Unit')
    header = ''.join(f'<th scope="col">{label}</th>' for label in labels)
    table = '\n'.join('<tr>' + ''.join(
        f'<td>{escape(str(value)) if value is not None else "–"}</td>' for value in row.values()) + '</tr>'
        for row in metrics['metrics'])
    return f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Curve 3pool | Effect metrics</title>
<style>
body{{font:16px/1.55 system-ui,sans-serif;color:#142639;background:#fff;margin:0 auto;padding:2rem;width:min(90%,1080px)}}
h1{{line-height:1.15;margin-bottom:.35em}}p{{max-width:75ch}}a{{color:#087b72}}
.figures{{display:grid;max-width:820px;gap:1.15rem}}
figure{{margin:0}}img{{width:100%;display:block}}figcaption{{color:#455c71;padding:.2rem .6rem}}
.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:.85rem}}
th,td{{text-align:left;padding:.36rem .55rem;border-bottom:1px solid #d7e2e8}}th{{background:#e6eef2}}
summary{{cursor:pointer;font-weight:700;margin:1rem 0}}
</style>
<main>
<h1>Curve 3pool investigation</h1>
<p>Effect metrics from the frozen G/E study, controlled arithmetic tests and historical decision replay.</p>
<p><a href="metrics.csv">Download CSV</a> · <a href="metrics.json">Download JSON with input hashes</a></p>
<div class="figures">{sections}</div>
<details><summary>Show all metrics and denominators</summary>
<div class="table"><table><thead><tr>{header}</tr></thead><tbody>{table}</tbody></table></div>
</details>
<p>A dash indicates an unavailable value or an inapplicable denominator. Counts and sample units vary by experiment.</p>
</main></html>
'''


def build(root, output, *, metrics_only=False):
    if not metrics_only:
        try:
            from plots import CHART_TITLES, render
        except ModuleNotFoundError as exc:
            if exc.name != 'matplotlib':
                raise
            raise ValueError('Install audit/effect_metrics/requirements.txt to render figures, '
                             'or use --metrics-only to export CSV and JSON.') from exc
    metrics = measure(root)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'metrics.json').write_text(json.dumps(metrics, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    with (output / 'metrics.csv').open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(metrics['metrics'][0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(metrics['metrics'])
    if not metrics_only:
        render(metrics, output)
        (output / 'index.html').write_text(browser_report(CHART_TITLES, metrics), encoding='utf-8')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2],
                        help='Extracted project directory with the verified archive records')
    parser.add_argument('--output', type=Path, required=True, help='A new directory for the report')
    parser.add_argument('--metrics-only', action='store_true',
                        help='Export CSV and JSON without installing Matplotlib')
    args = parser.parse_args()
    try:
        build(args.root, args.output, metrics_only=args.metrics_only)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
