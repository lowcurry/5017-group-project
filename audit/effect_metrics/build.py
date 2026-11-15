"""Export effect metrics, shareable SVG charts and a browser report from archived results."""
from argparse import ArgumentParser
import csv
from html import escape
from itertools import groupby
import json
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from metrics import measure


INK = '#142639'
MUTED = '#455c71'
GRID = '#d7e2e8'
BLUE = '#335e99'
TEAL = '#087b72'
RED = '#ad3934'
GRAY = '#66778b'
EVENTS = {'ftx_2022': 'FTX, Nov 2022', 'usdc_2023': 'USDC, Mar 2023'}


class Figure:
    def __init__(self, title, subtitle, height):
        self.root = Element('svg', {
            'xmlns': 'http://www.w3.org/2000/svg', 'width': '820', 'height': str(height),
            'viewBox': f'0 0 820 {height}', 'role': 'img', 'aria-labelledby': 'figure-title figure-desc',
            'font-family': 'Arial, sans-serif',
        })
        SubElement(self.root, 'title', id='figure-title').text = title
        SubElement(self.root, 'desc', id='figure-desc').text = subtitle
        self.rect(0, 0, 820, height, '#f4f8fa', radius=16)
        self.label(26, 40, title, size=24, bold=True)
        self.label(26, 65, subtitle, color=MUTED)

    def shape(self, tag, **attributes):
        return SubElement(self.root, tag, {
            name.replace('_', '-'): f'{value:g}' if isinstance(value, (int, float)) else value
            for name, value in attributes.items()
        })

    def label(self, x, y, value, *, size=14, color=INK, align='start', bold=False):
        self.shape('text', x=x, y=y, fill=color, font_size=size,
                   font_weight='700' if bold else '400', text_anchor=align).text = str(value)

    def rect(self, x, y, width, height, color, radius=0):
        self.shape('rect', x=x, y=y, width=width, height=height, fill=color, rx=radius)

    def line(self, x1, y1, x2, y2, color=GRID):
        self.shape('line', x1=x1, y1=y1, x2=x2, y2=y2, stroke=color)

    def dot(self, x, y, color):
        self.shape('circle', cx=x, cy=y, r=4, fill=color, stroke='white', stroke_width=0.75)

    def write(self, path):
        path.write_bytes(tostring(self.root, encoding='utf-8', xml_declaration=True) + b'\n')


def horizontal(fig, rows, *, start=130, left=246, width=480):
    for percent in range(0, 101, 25):
        x = left + width * percent / 100
        fig.line(x, start - 15, x, start + 63 * len(rows) - 12)
        fig.label(x, start - 24, f'{percent}%', size=12, align='middle', color=MUTED)
    for index, (name, series) in enumerate(rows):
        top = start + index * 63
        fig.label(26, top + 21, name, size=14, bold=True)
        for offset, (data, color) in enumerate(zip(series, (BLUE, TEAL))):
            y = top + offset * 23
            value = data['value']
            if value is None:
                fig.label(left + 6, y + 13, 'N/A', color=MUTED, size=12)
                continue
            extent = width * value / 100
            if extent:
                fig.rect(left, y, extent, 16, color, radius=3)
            fig.label(left + extent + 7, y + 13, f"{data['numerator']}/{data['denominator']}",
                      color=color, size=12, bold=True)


def coverage_chart(find):
    fig = Figure('How many correct components make it through?',
                 'Formal G/E comparison across the same scheduled tasks', 374)
    fig.label(26, 93, 'G  Ordinary prompting', color=BLUE, bold=True)
    fig.label(246, 93, 'E  Evidence-guided prompting', color=TEAL, bold=True)
    horizontal(fig, [
        (title, [find('formal', 'scheduled', 'overall', group, item) for group in ('G', 'E')])
        for title, item in (('Raw correct', 'correct'), ('Correct and reproducible', 'reproducible_correct'),
                            ('Correct and published', 'published_correct'))
    ], start=144)
    fig.label(26, 354, 'Denominator: scheduled answerable components per group, including budget stops.',
              size=13, color=MUTED)
    return fig


def guard_chart(find):
    fig = Figure('Do shared arithmetic errors get published?',
                 'The same six paired fault cases under the old and new publication rules', 374)
    fig.label(26, 93, 'Before  Existing verifier', color=BLUE, bold=True)
    fig.label(246, 93, 'After  Independent arithmetic added', color=TEAL, bold=True)
    horizontal(fig, [
        (title, [find('arithmetic_faults', case, 'overall', stage, 'published')
                 for stage in ('before', 'after')])
        for title, case in (('Correct controls', 'clean'), ('Wrong reports', 'faulty_report'),
                            ('Shared verifier error', 'shared_verifier_bug'))
    ], start=144)
    fig.label(26, 354, 'Each row measures the share of cases published. The errors were injected offline.',
              size=13, color=MUTED)
    return fig


def decision_chart(find):
    fig = Figure('Verification trades automation for review',
                 '36 scheduled historical research decisions from the same report records', 290)
    for x, title, color in ((26, 'Correct action', TEAL), (193, 'Wrong action', RED), (353, 'Sent to review', GRAY)):
        fig.rect(x, 86, 13, 13, color, radius=2)
        fig.label(x + 19, 98, title, color=MUTED)
    for y, stage, title in ((122, 'raw', 'Raw report'), (183, 'verified', 'Verified report')):
        fig.label(26, y + 20, title, bold=True)
        x = 166
        for name, color in zip(('correct', 'wrong', 'deferred'), (TEAL, RED, GRAY)):
            item = find('research_decisions', 'scheduled', 'overall', stage, name)
            value = item['numerator']
            width = 611 * value / item['denominator']
            if width:
                fig.rect(x, y, width, 28, color)
                if width >= 28:
                    fig.label(x + width / 2, y + 19, value, color='white', align='middle', bold=True)
            x += width
        counts = [find('research_decisions', 'scheduled', 'overall', stage, name)['numerator']
                  for name in ('correct', 'wrong', 'deferred')]
        fig.label(166, y + 44, f'{counts[0]} correct  ·  {counts[1]} wrong  ·  {counts[2]} sent to review',
                  color=MUTED, size=12)
    fig.label(26, 268, 'The replay counts review referrals. It does not observe what happened during review.',
              color=MUTED, size=13)
    return fig


def quote_chart(points):
    fig = Figure('How large is the quote extrapolation error?',
                 '100,000 USDC to USDT  ·  Small-order extrapolation minus size-matched quote', 360)
    data = [(event, list(items)) for event, items in groupby(points, key=lambda point: point['event_id'])]
    errors = [float(point['extrapolation_error_usdt']) for point in points]
    low, high = min(0, min(errors)), max(0, max(errors))
    if low == high:
        high = low + 1
    left, width = 238, 535

    def x(value):
        return left + width * (value - low) / (high - low)

    for i in range(5):
        value = low + (high - low) * i / 4
        tick = x(value)
        fig.line(tick, 103, tick, 260)
        fig.label(tick, 282, f'{value:.2f}', size=12, align='middle', color=MUTED)
    for index, (event, items) in enumerate(data):
        y = 130 + index * 85
        fig.label(26, y, EVENTS.get(event, event), size=15, bold=True)
        fig.label(26, y + 21, f'{len(items)} hourly targets', color=MUTED, size=12)
        for offset, point in enumerate(items):
            value = float(point['extrapolation_error_usdt'])
            fig.dot(x(value), y - 18 + 6 * (offset % 9), (BLUE, TEAL)[index % 2])
    fig.label(506, 306, 'Quote gap (USDT, linear scale)', align='middle', color=MUTED)
    fig.label(26, 336, 'Each dot is one historical target. Vertical jitter within a row separates nearby points.',
              color=MUTED, size=13)
    return fig


def chart_set(metrics):
    indexed = {(row['experiment'], row['cohort'], row['scope'], row['group'], row['metric']): row
               for row in metrics['metrics']}

    def find(*key):
        return indexed[key]

    return {
        'coverage.svg': ('Formal correct coverage', coverage_chart(find)),
        'arithmetic-faults.svg': ('Publication of arithmetic fault cases', guard_chart(find)),
        'research-decisions.svg': ('Research actions before and after verification', decision_chart(find)),
        'quote-errors.svg': ('Historical quote error by event', quote_chart(metrics['quote_points'])),
    }


def browser_report(charts, metrics):
    sections = '\n'.join(f'<figure><img src="{name}" alt="{escape(title)}">'
                         f'<figcaption>{escape(title)}</figcaption></figure>'
                         for name, (title, _) in charts.items())
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


def build(root, output):
    metrics = measure(root)
    charts = chart_set(metrics)
    rendered_report = browser_report(charts, metrics)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'metrics.json').write_text(json.dumps(metrics, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    with (output / 'metrics.csv').open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(metrics['metrics'][0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(metrics['metrics'])
    for name, (_, figure) in charts.items():
        figure.write(output / name)
    (output / 'index.html').write_text(rendered_report, encoding='utf-8')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2],
                        help='Extracted project directory with the verified archive records')
    parser.add_argument('--output', type=Path, required=True, help='A new directory for the report')
    args = parser.parse_args()
    try:
        build(args.root, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
