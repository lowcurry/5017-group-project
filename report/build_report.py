"""Build the reader-first MSBD5017 report from public repository archives."""
from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Flowable, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

A4_W, A4_H = A4
MARGIN = 46
WIDTH = A4_W - 2 * MARGIN - 12
NAVY = colors.HexColor("#17283B")
TEAL = colors.HexColor("#08736E")
SLATE = colors.HexColor("#45596B")
PALE = colors.HexColor("#EDF5F4")
ALT = colors.HexColor("#F2F5F8")
LINE = colors.HexColor("#CFDAE1")
WHITE = colors.white
TITLE = "An Auditable Investigation Agent for Curve 3pool"
WINDOWS = ("2023-06-15", "2023-10-23", "2024-01-11")
HOLDOUT = Path("audit/holdout_validation")
RECOVERY = Path("audit/evidence_recovery/results")
FORMAL = Path("baseline/experiments/formal_v1/result_summary.json")
POLICIES = Path("decision_cost/results/policy-summary.csv")


def style(name, size, lead, color=NAVY, font="Helvetica", after=6, before=0):
    return ParagraphStyle(name, fontName=font, fontSize=size, leading=lead,
                          textColor=color, spaceAfter=after, spaceBefore=before)


STYLES = {
    "kicker": style("kicker", 10, 14, TEAL, "Helvetica-Bold", after=9),
    "title": style("title", 30, 34, font="Helvetica-Bold", after=10),
    "h1": style("h1", 24, 28, font="Helvetica-Bold", after=12),
    "h2": style("h2", 13, 17, TEAL, "Helvetica-Bold", before=8),
    "body": style("body", 10.6, 15.1),
    "lead": style("lead", 12.1, 17, after=11),
    "small": style("small", 9.05, 12.7, SLATE),
    "caption": style("caption", 8.7, 12.2, SLATE, after=9),
    "cell": style("cell", 9.35, 12.7, after=0),
    "head": style("head", 9.35, 12, WHITE, "Helvetica-Bold", after=0),
    "code": style("code", 8.2, 11.8, SLATE, "Courier"),
    "card": style("card", 11, 15.5, NAVY, after=0),
}


def read_json(root, path):
    return json.loads((root / path).read_text(encoding="utf-8"))


def read_rows(root, path):
    with (root / path).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def elapsed(seconds):
    mins, secs = divmod(int(seconds), 60)
    return f"{mins} min {secs:02d} s"


def pair(a, b):
    return f"{a} to {b}"


class Workflow(Flowable):
    """Draw a static vector flowchart with a separate evidence recovery branch."""

    def __init__(self):
        super().__init__()
        self.width = WIDTH
        self.height = 272

    def draw(self):
        c = self.canv
        # The first three stages are shared. Two branches rejoin at the decision.
        self._arrow(c, 246, 227, 246, 215)
        self._arrow(c, 246, 181, 246, 169)
        c.setStrokeColor(TEAL)
        c.setLineWidth(1)
        c.line(246, 131, 246, 123)
        c.line(112, 123, 380, 123)
        self._arrow(c, 112, 123, 112, 96)
        self._arrow(c, 380, 123, 380, 96)
        self._arrow(c, 112, 57, 112, 47)
        self._arrow(c, 380, 57, 380, 47)
        self._node(c, 138, 227, 216, 34, "1  Pin blocks and capture two sources",
                   "Store raw balances, quotes, units and block IDs")
        self._node(c, 138, 181, 216, 34, "2  Generate structured claims",
                   "Give each claim a query, value and evidence IDs")
        self._node(c, 138, 131, 216, 38, "3  Recompute the original claim",
                   "Match query, value, unit and evidence scope")
        self._node(c, 9, 57, 219, 39, "4A  Full verification",
                   "Check both sources and publish passing claims")
        self._node(c, 262, 57, 219, 39, "4B  Full verification with recovery",
                   "Fill eligible citation gaps and rerun the full gate", accented=True)
        self._node(c, 94, 8, 303, 39, "5  Act on the checked publication",
                   "Use a supported decision or refer it for human review")
        c.setFont("Helvetica", 8.2)
        c.setFillColor(TEAL)
        c.drawString(122, 105, "Baseline path")
        c.drawRightString(370, 105, "Only eligible citation gaps are repaired")

    @staticmethod
    def _node(c, x, y, w, h, header, sub, accented=False):
        c.setFillColor(PALE if accented else ALT)
        c.setStrokeColor(TEAL if accented else LINE)
        c.setLineWidth(0.7)
        c.roundRect(x, y, w, h, 5, stroke=1, fill=1)
        c.setFillColor(TEAL if accented else NAVY)
        c.setFont("Helvetica-Bold", 9.0)
        c.drawString(x + 8, y + h - 13, header)
        c.setFillColor(SLATE)
        c.setFont("Helvetica", 8.15)
        c.drawString(x + 8, y + h - 25, sub)

    @staticmethod
    def _arrow(c, x1, y1, x2, y2):
        c.setStrokeColor(TEAL)
        c.setLineWidth(1)
        c.line(x1, y1, x2, y2)
        if x1 == x2:
            c.line(x2, y2, x2 - 2.6, y2 + 4)
            c.line(x2, y2, x2 + 2.6, y2 + 4)
        else:
            direction = 4 if x2 < x1 else -4
            c.line(x2, y2, x2 + direction, y2 + 2.6)
            c.line(x2, y2, x2 + direction, y2 - 2.6)


def build(root, output, edition):
    formal = read_json(root, FORMAL)
    groups = formal["by_group"]
    policies = {r["policy"]: r for r in read_rows(root, POLICIES)}
    recovery = read_json(root, RECOVERY / "recovery_summary.json")
    recovered_formal = recovery["formal_36_runs"]["totals"]
    replay = recovery["decision_replay_36"]
    fault = recovery["fault_safety_gates"]
    alert = read_json(root, HOLDOUT / "alert-metrics.json")
    billing = read_json(root, HOLDOUT / "billing-reconciliation.json")
    human = read_json(root, HOLDOUT / "human-timing-observed.json")
    timing = read_json(root, HOLDOUT / "timing-summary.json")
    summaries = {day: read_json(root, HOLDOUT / day / "summary.json") for day in WINDOWS}
    n_cases = sum(x["arms"]["baseline"]["cases"] for x in summaries.values())
    arms = ("baseline", "recovery")
    totals = {a: {field: sum(x["arms"][a][field] for x in summaries.values())
                  for field in ("to_human", "published_correct_claims", "auto_error")}
              for a in arms}
    refs = pair(*(totals[a]["to_human"] for a in arms))
    pubs = pair(*(totals[a]["published_correct_claims"] for a in arms))
    money = f'{Decimal(billing["recorded_charge"]):.8f}'
    recorded = human["observations"]
    minutes = elapsed(sum(x["seconds"] for x in recorded))
    timing_rows = list(timing["events"].values())
    t1, t2 = (f'{x["median_incremental_wall_ms"]:.2f}' for x in timing_rows)
    g, e = (f'{groups[k]["reproducible_correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))
    jdate = WINDOWS[0]
    example = next(r for r in read_json(root, HOLDOUT / jdate / "recovery-trace.json")
                   if r["run_id"] == "H02_G_1")
    peak = next(c for c in example["recovery_publication"]["claims"] if c["slot_id"] == "S2")
    quote = next(c for c in example["recovery_publication"]["claims"] if c["slot_id"] == "S3")
    recovered_ids = example["recovery_records"]["S2"]["recovered_evidence_ids"]
    peak_hour, quote_hour = (c["target_timestamp_utc"][11:16] for c in (peak, quote))
    flow = []

    def p(content, kind="body"):
        flow.append(Paragraph(content, STYLES[kind]))

    def section(kicker, title):
        p(kicker, "kicker")
        p(title, "h1")

    def table(headers, rows, widths):
        values = [[Paragraph(x, STYLES["head"]) for x in headers]]
        values.extend([Paragraph(str(x), STYLES["cell"]) for x in row] for row in rows)
        t = Table(values, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TEAL),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ALT]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, -1), (-1, -1), 0.6, LINE),
        ]))
        flow.extend((Spacer(1, 3), t, Spacer(1, 8)))

    def card(content):
        t = Table([[Paragraph(content, STYLES["card"])]], colWidths=[WIDTH])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), PALE),
            ("LINEBEFORE", (0, 0), (0, 0), 3, TEAL),
            ("LEFTPADDING", (0, 0), (0, 0), 12),
            ("RIGHTPADDING", (0, 0), (0, 0), 12),
            ("TOPPADDING", (0, 0), (0, 0), 9),
            ("BOTTOMPADDING", (0, 0), (0, 0), 9),
        ]))
        flow.extend((t, Spacer(1, 8)))

    def new_page(kicker, title):
        flow.append(PageBreak())
        section(kicker, title)

    p("MSBD 5017  /  TRADE 2  /  FINAL PROJECT", "kicker")
    p(TITLE, "title")
    p("WANG Hanyang &nbsp;&nbsp; HE Yuequn &nbsp;&nbsp; WU Yuxuan", "small")
    p(edition, "small")
    flow.append(Spacer(1, 10))
    p("<b>The problem.</b> Curve 3pool is an Ethereum pool for exchanging DAI, USDC and USDT. "
      "A model can identify when the pool looked most imbalanced. If it picks the wrong hour, "
      "an analyst checking the next-hour quote will inspect the wrong block.", "lead")
    p("<b>What we built.</b> An investigation agent that produces claims tied to specific blocks, "
      "a publication gate that recomputes the claims, and a decision workflow that can send uncertain cases to a reviewer.")
    p("<b>What changed after feedback.</b> We separated verification from generation, fixed the scoring "
      "denominator and checked references. We also evaluated when risk justifies human intervention "
      "and whether a lightweight decision model or limited post-training improves that choice.")
    p("Three findings", "h2")
    card(f"<b>1. Evidence guidance improved reproducible coverage.</b> Of the scheduled answer slots, "
         f"{g} were correct and reproducible with the ordinary prompt and {e} with evidence guidance.")
    card(f"<b>2. Evidence recovery reduced referrals for human review.</b> Across {n_cases} paired decisions "
         f"in three held-out windows, referrals fell from {refs} and correct published claims rose "
         f"from {pubs}. We observed zero automatic decision errors in both paths.")
    card(f"<b>3. The simpler decision policies did better in this cost scenario.</b> The fixed rule "
         f"and logistic regression averaged about {float(policies['rule']['average_cost']):.2f} units per decision. "
         f"Both neural variants averaged about {float(policies['tuned_encoder']['average_cost']):.2f}.")
    p("The results cover one pool and a small number of events. Repeated runs within an event "
      "share the same market evidence.", "small")

    new_page("01 / SYSTEM", "From a block to a decision")
    p("At each UTC target, we record an Ethereum block and the three token balances, then preserve "
      "the query inputs and quotes needed to replay the calculation. Alchemy and dRPC provide two "
      "recorded views of the same chain. We verify agreement on the bound fields.")
    flow.append(Workflow())
    p("<b>Figure 1.</b> Both holdout paths use the same model reports. The highlighted branch adds "
      "deterministic citation recovery before the full gate. Both branches withhold claims that fail "
      "source, arithmetic or dependency checks. Other withdrawals "
      "continue to the decision stage without a published claim.", "caption")
    p("A real replay: select the peak hour, then check the quote", "h2")
    p(f"On {jdate}, report H02_G_1 found the correct sampled maximum at <b>{peak_hour} UTC</b> "
      f"but cited only the winning hour. Establishing a maximum required all {len(recovered_ids)} allowed "
      "hourly observations. Full verification withheld the claim and the dependent next-hour quote, "
      "so the follow-up decision went to a reviewer.")
    p(f"The recovery pass completed the citation set on a copy of the report and repeated the checks. "
      f"It restored the peak and the {quote_hour} UTC quote of "
      f"<b>{Decimal(quote['value']):.6f} USDT per USDC</b> for a 100,000 USDC input. "
      "The decision then used the correct follow-up hour automatically.")
    p("<b>What the hash proves.</b> The local chain demo recorded and retrieved the evidence manifest "
      "digest. It checks package integrity. Numerical correctness still depends on source checks and recomputation.")
    p("Trace: audit/holdout_validation/2023-06-15/recovery-trace.json, run H02_G_1. "
      "Integrity demo: baseline/contracts/anchor.mjs and baseline/results/current_chain/anchor.json.", "small")

    new_page("02 / COURSE FEEDBACK", "How feedback changed the design")
    p("The written proposal feedback asked us to measure error reduction alongside correct coverage "
      "and make verification independent in the implementation. Later, the professor asked us to "
      "price human intervention and try faster decision models with limited post-training.")
    table(["Feedback", "Engineering response", "Validation in this report"], [
        ["Independent checking should catch shared errors.",
         "Check a second RPC capture and run separate exact-fraction arithmetic on eligible balance metrics.",
         "Shared fault injection and matched clean controls, below."],
        ["A verifier can look good by answering less. Compare fairly and check the answer key.",
         "Fix required slots and budget limits. Score correctness, citations, omissions and refusals separately.",
         "Scheduled G/E coverage and human reference provenance, below."],
        ["Choose when to intervene by weighing risk and cost.",
         "Compare release, verification and human review with an explicit loss model.",
         "Historical routing comparison, Section 03."],
        ["Try fast decisions and modest post-training.",
         "Compare a fixed rule, logistic regression and two small encoder variants.",
         "Leave-one-event-out evaluation, Section 03."],
    ], [132, 192, WIDTH-324])
    p("<b>Table 1.</b> The first two rows summarize written proposal feedback. "
      "The last two summarize the professor's later suggestions.", "caption")
    p("Fair comparison on the frozen historical tasks", "h2")
    p("The development benchmark uses six tasks over the November 2022 FTX and March 2023 USDC "
      "windows, with 51 recorded hourly snapshots in total.", "small")
    table(["Measure", "Ordinary prompt (G)", "Evidence guidance (E)"], [
        ["Reports submitted / scheduled", *(f'{groups[k]["successful_submissions"]}/{groups[k]["scheduled_runs"]}' for k in ("G", "E"))],
        ["Correct, before publication", *(f'{groups[k]["correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))],
        ["Correct and reproducible", g, e],
        ["Correct after guarded publication", *(f'{groups[k]["published_correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))],
    ], [WIDTH-205, 101, 104])
    p("<b>Table 2.</b> Both prompts used the same DeepSeek Flash model, six tasks, tools, archived "
      "evidence and budget ceilings. Sixteen answerable slots repeated three times give 48 opportunities per arm. "
      "G's two budget stops remain in that denominator. On the 16 matched completed pairs, raw correctness "
      "was 40/43 for each arm and reproducible correctness was 34/43 versus 39/43.", "caption")
    p("<b>Reference provenance.</b> The historical freeze used a consolidated human reference workbook. "
      "Reviewer isolation at freeze was unverified. In each later holdout window, two human reference "
      "reviews agreed with both-source recomputation on all nine items without adjudication.")
    p("<b>Shared-error test.</b> When V1 and V2 shared the injected error, all six wrong claims passed. "
      "Independent publication arithmetic withheld all six and retained all six clean controls. "
      "The extra arithmetic covers selected balance metrics; quotes remain subject to V2.", "small")
    p("Evidence: baseline/experiments/formal_v1/result_summary.json and "
      "baseline/audit/arithmetic_faults/README.md.", "small")

    new_page("03 / INTERVENTION", "When should a person step in?")
    p("The professor asked us to include the cost of human safeguards. We compare leaving a report "
      "to drive a decision, verifying the evidence first, and referring it for human review. "
      "A low predicted loss can justify direct release. A severe potential miss can justify "
      "verification or referral even when accuracy looks high, and both interventions take resources.")
    p("The fixed-rule and learned policies minimize expected scenario cost using inputs available before "
      "verification. The two controls use a fixed approach: release available raw actions or verify every case. "
      "The same 36 historical decisions cover share direction, peak follow-up hour and concentration alerts.")
    p("Does a lightweight learned model help?", "h2")
    p("We evaluated logistic regression, a frozen tabular encoder with a trained head, and a variant that "
      "also tunes the final encoder layer. Each learned policy is evaluated on one historical event using "
      "the other event for training. Repeated runs from the same event stay together.")
    labels = (
        ("raw", "Direct release"), ("verified", "Full verification"), ("rule", "Fixed risk and cost rule"),
        ("logistic", "Logistic regression"), ("frozen_head", "Frozen encoder + trained head"),
        ("tuned_encoder", "Last encoder layer + trained head"),
    )
    table(["Policy", "Human referrals", "Automatic errors", "Cost / decision"], [
        [label, r["to_human"], r["auto_error"], f'{float(r["average_cost"]):.2f}']
        for key, label in labels for r in (policies[key],)
    ], [WIDTH-247, 82, 82, 83])
    p("<b>Table 3.</b> Counts refer to 36 scheduled historical decisions. Average costs are hypothetical "
      "units. The default scenario assumes 5 minutes per review at 1 unit/min, 0.2 units per verification and "
      "2% residual human error. A missed concentration alert costs 200 units; a false alert costs 40.", "caption")
    p("<b>What we learned.</b> The fixed rule and logistic regression tied at the displayed precision. "
      "Both neural variants referred two more cases and cost more under the frozen assumptions. "
      "This replay provides no evidence of a cost advantage from final-layer tuning.")
    p("Full verification blocked the one observed wrong raw action but added nine referrals. "
      "It breaks even against raw release when the avoided loss exceeds "
      f'{replay["break_even_raw_to_verified"]["break_even_avoided_loss_perfect_review"]:.1f} scenario units with perfect review, or '
      f'{replay["break_even_raw_to_verified"]["break_even_avoided_loss_configured_review"]:.1f} with residual review error. '
      "The ranking depends on those assumptions.", "small")
    p("Implementation and inputs: decision_cost/evaluation.py, decision_cost/models.py and decision_cost/scenario.json.", "small")

    new_page("04 / EVIDENCE RECOVERY", "Fewer referrals on the same reports")
    p("While inspecting the historical withdrawals, we found correct answers that cited only part "
      "of the required time window. Our follow-up improvement completes the exact missing citations "
      "only when a claim already matches the recomputed value and units and supplies a valid subset. "
      "The recovered copy must pass the second-source check, applicable independent arithmetic, and dependencies.")
    table(["Test stage", "What it measured", "Observed outcome"], [
        ["Historical replay", "Recovery on the archived 36 scheduled model runs.",
         f'{recovered_formal["total_recovered_correct"]} correct claims restored '
         f'({recovered_formal["direct_attribution_recoveries"]} direct, '
         f'{recovered_formal["cascade_dependency_recoveries"]} from dependencies).'],
        ["Fault injection", "Whether the recovery path would release a non-clean case.",
         f'{fault["total_fault_false_accepts"]} false accepts across 56 non-clean cases; 34 clean controls kept.'],
        ["Held-out comparison", "Both paths process the same model reports in each window.",
         f'{n_cases} paired decisions; results by window below.'],
    ], [112, 189, WIDTH-301])
    p("Three-window confirmation", "h2")
    rows = []
    for day, result in summaries.items():
        base, rec = (result["arms"][a] for a in arms)
        rows.append([day, base["cases"], pair(base["to_human"], rec["to_human"]),
                     pair(base["auto_error"], rec["auto_error"]),
                     pair(base["published_correct_claims"], rec["published_correct_claims"])])
    rows.append(["<b>Total</b>", n_cases, refs, pair(*(totals[a]["auto_error"] for a in arms)), pubs])
    table(["Window", "Paired decisions", "Referrals", "Auto. errors", "Correct claims"], rows,
          [102, 90, 92, 89, WIDTH-373])
    p("<b>Table 4.</b> Values show full verification followed by full verification with recovery. "
      "Each window has 18 decisions, derived from three task templates, two prompts and three repetitions. "
      "Correct claims are counted separately since a report can contain multiple claims.", "caption")
    p("Concentration alerts", "h2")
    neg = alert["by_window"][jdate]["baseline"]
    ap = alert["pooled_decision_counts"]
    p(f"For both paths, the observed false-positive rate was <b>{neg['false_positives']}/"
      f"{neg['reference_no_alert']}</b>, with no negative referrals. All six reference negatives came from one "
      "task repeated in the June window. The two newer windows have no reference negatives, so their "
      f"false-positive rates are undefined. Across their 12 reference alerts, correct automatic alerts rose "
      f"from {ap['baseline']['true_positives']} to {ap['recovery']['true_positives']}.")
    p("Initially, seven June runs received HTTP 402. Retrying those seven yielded complete reports, "
      "and the original eleven successful reports were retained. Both paths used the resulting identical archive.", "small")
    p("Evidence: audit/evidence_recovery/results/ and audit/holdout_validation/.", "small")

    new_page("05 / MEASURED RESOURCES", "What the system actually used")
    p("The same model outputs feed both publication paths. Recovery therefore adds local verification work "
      "without additional model calls. We keep billed API charges, observed reviewer time and processing "
      "time in their original units.")
    table(["Resource", "Recorded result", "Measurement scope"], [
        ["DeepSeek billing", f"<b>CNY {money}</b> for the three holdouts. Recovery added zero model requests.",
         f'The export matches {billing["provider_usage"]["request_count"]} requests with reported usage. '
         f'{billing["requests_without_reported_usage"]} failed requests had no reported token count.'],
        ["Manual review", f'<b>{minutes}</b> across two correct reviews '
         f'({elapsed(recorded[0]["seconds"])} and {elapsed(recorded[1]["seconds"])}).',
         "Two baseline referrals in the June window. These durations are not applied to the other windows."],
        ["Local verification", f"<b>+{t1} ms</b> in October and <b>+{t2} ms</b> in January, "
         "median paired wall time per report.",
         f'{sum(x["pairs"] for x in timing_rows)} interleaved pairs from {timing["rounds"]} timed rounds '
         "on the two newer windows. Excludes model generation and human review."],
    ], [102, 190, WIDTH-292])
    p("<b>Table 5.</b> Billing is an observed platform charge for data collection. The two verification "
      "paths reuse that same collection. Human time is not converted to currency because a labor rate is unavailable.", "caption")
    p("How far the results generalize", "h2")
    p("Three held-out windows provide more evidence than one, but all use the same pool, three task types "
      "and one model. Repeated cases sharing an event should not be treated as independent events. "
      "The negative alert sample is especially narrow, and zero observed errors does not establish zero risk.")
    p("The benchmark measures rule-based research decisions and block-pinned quotes. It does not measure "
      "live trades, fills, gas, returns, or actual net savings. The historical scenario shows how a decision could "
      "change as review costs and error losses vary.")
    p("Recorded inputs: audit/holdout_validation/billing-reconciliation.json, "
      "human-timing-observed.json and timing-summary.json in the same directory.", "small")

    new_page("06 / REPRODUCIBILITY", "Trace each result to its evidence")
    p("Use the checked-in archives for an offline replay. The two holdout commands below reconstruct "
      "publication from the saved reports and recalculate alert outcomes from the saved decision tables.")
    table(["Finding", "Inputs and results", "Check from repository root"], [
        ["Historical reproducible coverage and shared arithmetic errors",
         "baseline/experiments/formal_v1/result_summary.json; baseline/audit/arithmetic_faults/",
         "Commands 1 and 2"],
        ["Six historical routing policies",
         "decision_cost/scenario.json; decision_cost/results/policy-summary.csv",
         "Command 3"],
        ["Recovery and fault cases",
         "audit/evidence_recovery/results/",
         "Command 4"],
        ["Three-window decisions and alert statistics",
         "audit/holdout_validation/ (dated decisions.csv, runs.json, alert-metrics.json)",
         "Commands 5 and 6"],
        ["Measured billing, reviewer time and verification time",
         "audit/holdout_validation/ (billing-reconciliation.json, human-timing-observed.json, timing-summary.json)",
         "Inspect the reconciled evidence files"],
    ], [122, 252, WIDTH-374])
    p("Offline commands", "h2")
    p("Install decision_cost/requirements.txt for the historical cost and recovery scripts. "
      "Copy the block into a shell from the repository root. The first line creates a temporary directory. "
      "The six commands then follow in the order listed above.", "small")
    commands = (
        'run_dir="$(mktemp -d)"',
        '(cd baseline && python3 -B -m curve_audit replay)',
        '(cd baseline && python3 -B audit/arithmetic_faults/replay_publication_guard.py .)',
        'python3 -B decision_cost/evaluation.py --output "$run_dir/cost"',
        'python3 -B audit/evidence_recovery/run_all.py --output "$run_dir/recovery"',
        'python3 -B audit/holdout_validation/replay.py',
        'python3 -B audit/holdout_validation/alert_metrics.py \\',
        '    audit/holdout_validation "$run_dir/alerts.json"',
    )
    flow.append(Preformatted("\n".join(commands), STYLES["code"]))
    p("Compare the alert output with audit/holdout_validation/alert-metrics.json. "
      "Reproducing the archived results needs no model or RPC key. Running a new window requires "
      "fresh source captures, a frozen schedule and human reference review.", "small")

    new_page("APPENDIX / TECHNICAL NOTES", "Definitions, safeguards and sensitivity")
    p("What counts as reproducible?", "h2")
    p("A claim must match the frozen value, unit, time and query. Reproducibility also requires "
      "the expected evidence IDs, dataset digest and formula version. A reported maximum is a maximum "
      "over allowed hourly samples; a continuous-time maximum is not identified by that grid.")
    n_template = formal["template"]["reproducible_correct"]
    p(f"The deterministic template reproduced all {n_template} answerable components without a model call. "
      "It establishes what the computation can produce from the frozen evidence.", "small")
    p("Recovery eligibility", "h2")
    p("Recover only the reason incomplete_attribution. Require the bound dataset digest and correct "
      "formula version, a nonempty expected set, and a nonempty original set that is a strict subset. "
      "Repeat the checks and record the new IDs on a copied report. Failed or uncertain dependencies "
      "remain withheld.")
    p("Stress tests and uncertainty", "h2")
    p("The separate recovery fault audit used 32 frozen cases, 18 shared-arithmetic cases and 40 extension "
      "cases. The 90 total include 34 clean controls. Its zero false accepts is an observation on scripted cases.")
    p("For four historical policies, each had 0/11 observed errors on automatic concentration decisions. "
      "Nominal one-sided 95% upper bounds are 23.84% (Clopper-Pearson) and 15.69% (Jeffreys). "
      "The uniform Beta posterior mean is 7.69%, with a 22.09% one-sided upper bound. "
      "Two development events supply these repeated decisions, so the bounds rely on an unverified "
      "independence assumption. They concern historical automatic decision errors, which differ from the "
      "holdout false-positive rate among reference negatives.")
    p("Replacing the default 5% high-risk error assumption with 7.69%, 15.69% or 23.84% raised the "
      "unrecovered rule from 0.65 to 1.46 average scenario units. With recovery, the rule averaged "
      "0.71 and full verification averaged 0.78 under the same three assumptions. "
      "These are sensitivity comparisons under specified assumptions.", "small")
    p("Preserved historical scope", "h2")
    p("The historical quote screen compared a scaled small quote with the size-matched quote "
      "at 50 frozen blocks. The proxy overestimated output at all 50, but eligibility agreed at "
      "the five thresholds tested. See baseline/audit/decision_replay_20261112/results.json. "
      "See baseline/REPORT_ERRATA.md for clarifications to the original baseline report.", "small")
    p(f'Historical freeze SHA-256:<br/><font name="Courier" size="8.2">{formal["freeze_sha256"]}</font>', "small")

    def footer(c, doc):
        c.setTitle(TITLE)
        c.setAuthor("WANG Hanyang, HE Yuequn, WU Yuxuan")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(MARGIN, 38, A4_W-MARGIN, 38)
        c.setFillColor(SLATE)
        c.setFont("Helvetica", 8.3)
        c.drawString(MARGIN, 24, "MSBD 5017  /  CURVE 3POOL")
        c.drawRightString(A4_W-MARGIN, 24, str(doc.page))

    output.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(output), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                      topMargin=44, bottomMargin=53, invariant=True).build(
                          flow, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--edition", default="Final report | 23 November 2026")
    opts = parser.parse_args()
    build(opts.repo, opts.output or opts.repo / "MSBD5017_Final_Project_Report.pdf", opts.edition)
