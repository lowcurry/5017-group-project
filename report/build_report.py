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
HISTORICAL = Path("baseline/audit/decision_replay_20261112/results.json")


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
        self._node(c, 138, 227, 216, 34, "1  Record two sources at fixed blocks",
                   "Store raw balances, quotes, units and block IDs")
        self._node(c, 138, 181, 216, 34, "2  Generate structured claims",
                   "Give each claim a query, value and evidence IDs")
        self._node(c, 138, 131, 216, 38, "3  Recompute the claim",
                   "Check the value, unit and cited observations")
        self._node(c, 9, 57, 219, 39, "4A  Full verification",
                   "Check both sources and publish passing claims")
        self._node(c, 262, 57, 219, 39, "4B  Full verification with recovery",
                   "Complete eligible citations and verify again", accented=True)
        self._node(c, 94, 8, 303, 39, "5  Use the claim or refer the decision",
                   "Refer to a reviewer if a required claim is withheld")
        c.setFont("Helvetica", 8.2)
        c.setFillColor(TEAL)
        c.drawString(122, 105, "Verification only")
        c.drawRightString(370, 105, "Verification with evidence recovery")

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
    historical = read_json(root, HISTORICAL)["research"]["overall"]
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
    p("Curve 3pool is an Ethereum liquidity pool for DAI, USDC and USDT. If a report picks the "
      "wrong hour of peak imbalance, the analyst checks the next-hour quote at the wrong block. "
      "We built an agent whose balance and quote claims can be traced to recorded blocks and "
      "checked independently.", "lead")
    p("In the first historical replay, requiring verification prevented "
      f"{historical['wrong_sent_to_review']} automatic error and added "
      f"{historical['additional_reviews']} human referrals across {historical['decisions']} decisions. "
      "This trade-off made it important to measure both the errors caught and the correct decisions delayed.")
    p("Course feedback shaped the independent checks and the benchmark. The professor later asked us "
      "to weigh error losses against review costs and test lightweight decision models. Our review of "
      "withheld claims led to a separate improvement that completes missing evidence citations.")
    p("Results at a glance", "h2")
    card(f"<b>Evidence guidance made more answers reproducible.</b> The ordinary prompt yielded correct, "
         f"reproducible answers for {g} scheduled slots. Evidence guidance yielded {e} under the same "
         "tasks, model and budget.")
    card(f"<b>Completing citations reduced referrals.</b> On {n_cases} paired decisions across three held-out "
         f"windows, referrals fell from {refs} and correct published claims rose from {pubs}. "
         "We observed zero automatic errors in either path.")
    card(f"<b>Last-layer tuning did not lower the estimated cost.</b> In the default historical scenario, "
         f"the fixed rule and logistic regression each averaged {float(policies['rule']['average_cost']):.2f} "
         f"units per decision. Both neural variants averaged {float(policies['tuned_encoder']['average_cost']):.2f}.")
    p("The experiments cover one pool and one model. Repetitions within an event share the same market evidence.",
      "small")


    new_page("01 / EARLY RESULTS", "Early verification caught one error and added nine referrals")
    p("The October proposal focused on the March 2023 USDC depeg. We proposed a report that would "
      "trace each Curve 3pool balance and quote claim to specific blocks and calculations, a verifier "
      "that could check those claims, and a local demonstration of recording the evidence package hash.")
    p("The first measured version followed the 14 October written feedback and expanded the evaluation "
      "to two historical events. Its V2 verifier recomputed the claims and checked a second recorded "
      "source before publication. That version is the starting point for the later "
      "experiments on when to verify and whether to recover missing citations.")
    p("The same reports, with and without a publication check", "h2")
    p("We used the reports for three defined research decisions. Each case recorded a change in USDC "
      "share, selected a follow-up hour, or determined whether to issue a concentration alert.")
    table(["Decision input", "Correct automatic", "Wrong automatic", "Sent to review"], [
        [label, historical[key]["correct"], historical[key]["wrong"], historical[key]["deferred"]]
        for key, label in (("raw", "Available model output"), ("verified", "Claims published by V2"))
    ], [WIDTH-258, 86, 86, 86])
    p(f"<b>Table 1.</b> Both paths used the same {historical['decisions']} scheduled decisions from the "
      "November 2022 FTX and March 2023 USDC windows. The outcome columns sum to 36 in each row. "
      "The replay recorded referrals without measuring review time or reviewer outcomes.", "caption")
    p("Verification prevented an automatic label of falling USDC share when the recorded balances "
      f"showed a rise. It sent {historical['correct_sent_to_review']} otherwise correct decisions to review "
      "because their citations did not cover all the required observations. The first version applied "
      "the same check regardless of how costly an error would be.")
    p(f"The decision records are in {HISTORICAL.as_posix()}. Section 07 gives the offline replay command.", "small")


    new_page("02 / BENCHMARK DESIGN", "A fair test of independent verification")
    p("The written proposal feedback raised two evaluation risks. Shared arithmetic can let the same "
      "mistake pass twice, and scoring only submitted answers can reward omissions. The professor's "
      "later suggestions addressed a separate question about when to intervene.")
    table(["Question raised in feedback", "What we implemented", "How we tested it"], [
        ["Can two checks share the same mistake?",
         "Check a second RPC capture. Recompute selected balance metrics with separate exact-fraction arithmetic.",
         "Inject shared calculation errors and replay matched clean cases."],
        ["Can a system score well by answering less?",
         "Fix required answer slots and budgets. Score omissions and refusals against checked references.",
         "Compare prompts on the same scheduled tasks and completed pairs."],
        ["When do the stakes justify a review?",
         "Compare direct use, verification and referral under explicit error-loss and review-cost assumptions.",
         "Replay six policies on the historical decisions (Section 04)."],
        ["Can a small model route cases more cheaply?",
         "Compare a fixed rule, logistic regression and two encoder training setups.",
         "Hold out an entire historical event at a time (Section 04)."],
    ], [132, 192, WIDTH-324])
    p("<b>Table 2.</b> Rows 1 and 2 reflect written course feedback. Rows 3 and 4 reflect the professor's "
      "later suggestions.", "caption")
    p("Scoring every scheduled answer slot", "h2")
    p("The benchmark uses six tasks and 51 hourly snapshots from the two historical events. "
      "G and E use the same DeepSeek Flash model, tools, evidence and budget ceilings.", "small")
    table(["Outcome", "Ordinary prompt (G)", "Evidence guidance (E)"], [
        ["Reports submitted / scheduled", *(f'{groups[k]["successful_submissions"]}/{groups[k]["scheduled_runs"]}' for k in ("G", "E"))],
        ["Correct before verification", *(f'{groups[k]["correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))],
        ["Correct and reproducible", g, e],
        ["Correct and published", *(f'{groups[k]["published_correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))],
    ], [WIDTH-205, 101, 104])
    p("<b>Table 3.</b> Each prompt had 48 scheduled opportunities (16 answerable slots repeated three times), "
      "including those in G's two runs that stopped at the budget limit. In the 16 completed pairs, both "
      "prompts got 40/43 answers right. G made 34/43 reproducible and E made 39/43 reproducible.", "caption")
    p("<b>Reference answers.</b> The historical benchmark used a consolidated human workbook. "
      "Reviewer independence at the time of the freeze remains unverified. In each held-out window, "
      "two human reviews agreed with recomputation from both sources on all nine reference items.", "small")
    p("<b>Shared arithmetic.</b> All six injected errors passed when V1 and V2 shared the faulty calculation. "
      "The separate arithmetic check withheld all six and kept all six clean controls. V2 continues "
      "to handle quotes.", "small")
    p("The archives are baseline/experiments/formal_v1/result_summary.json and baseline/audit/arithmetic_faults/.",
      "small")


    new_page("03 / VERIFICATION WORKFLOW", "From recorded blocks to verified claims")
    p("For each UTC target, we record an Ethereum block, the three pool token balances and the "
      "inputs and outputs of quote queries. The verifier checks that the Alchemy and dRPC records "
      "agree on the fields used in the calculation. It recomputes each claim and checks the evidence "
      "needed to support its scope.")
    flow.append(Workflow())
    p("<b>Figure 1.</b> The two paths start from the same saved reports. Both check source agreement, "
      "arithmetic and claim dependencies. The path on the right fills eligible gaps and repeats "
      "the full check. Section 04 separately evaluates when to invoke verification.", "caption")
    p("Why one citation could not establish the peak", "h2")
    p(f"On {jdate}, report H02_G_1 picked the correct sampled peak at <b>{peak_hour} UTC</b> "
      f"but cited only that hour. A maximum requires checking all {len(recovered_ids)} eligible hourly "
      "observations. The verifier withheld the peak and its dependent next-hour quote, so the system "
      "referred the follow-up decision to a reviewer.")
    p("The recovery step added the required citations to a copy of the report and repeated the checks. "
      f"It restored the peak and the {quote_hour} UTC quote of "
      f"<b>{Decimal(quote['value']):.6f} USDT per USDC</b> for a 100,000 USDC input. "
      "The system could then select the follow-up hour automatically.")
    p("<b>What the chain records.</b> The local demo stores the evidence manifest hash so a reader can "
      "check whether the package has changed. Source checks and recomputation assess whether its "
      "numerical claims are correct.")
    p("The worked example is run H02_G_1 in audit/holdout_validation/2023-06-15/recovery-trace.json. "
      "The chain demo is in baseline/contracts/anchor.mjs and baseline/results/current_chain/anchor.json.", "small")


    new_page("04 / INTERVENTION", "When is verification worth the cost?")
    p("The professor asked us to weigh the loss from a wrong decision against the cost of intervening. "
      "A low expected loss can justify using a result as it stands. A potentially costly miss can justify "
      "checking sooner or involving a reviewer. Both checks and reviews consume resources.")
    p("We replayed the 36 historical decisions from Table 1. The controls either use available model outputs "
      "or verify every case. The other policies choose among those actions and immediate referral using "
      "task type and evidence fields available before verification.")
    p("Do lightweight models improve the choice?", "h2")
    p("We tested logistic regression, a frozen tabular encoder with a trained classification head, and "
      "an encoder with its final layer and head tuned together. Each learned policy trained on one "
      "event and was evaluated on the other. Repeated runs from one event stayed together.")
    labels = (
        ("raw", "Use available model output"), ("verified", "Verify every case"), ("rule", "Fixed risk-cost rule"),
        ("logistic", "Logistic regression"), ("frozen_head", "Frozen encoder, trained head"),
        ("tuned_encoder", "Final encoder layer and head tuned"),
    )
    table(["Policy", "Human referrals", "Automatic errors", "Cost / decision"], [
        [label, r["to_human"], r["auto_error"], f'{float(r["average_cost"]):.2f}']
        for key, label in labels for r in (policies[key],)
    ], [WIDTH-247, 82, 82, 83])
    p("<b>Table 4.</b> All policies faced 36 scheduled decisions. Costs are scenario units. The default "
      "assumes five minutes per human review at one unit per minute, 0.2 units per verification "
      "and a 2% chance of error after review. A missed concentration alert costs 200 units, and a false alert costs 40.",
      "caption")
    p("The fixed rule and logistic regression each referred two cases. Both neural variants referred four. "
      "None of the four policies made an observed automatic error. The modeled cost difference reflects "
      "more reviews and higher expected error losses for the neural policies. Tuning the final layer "
      "did not lower the estimated cost on these two events.")
    p("Under the scenario assumptions, verifying every case breaks even against using the available "
      "model output when the avoided loss reaches "
      f'{replay["break_even_raw_to_verified"]["break_even_avoided_loss_perfect_review"]:.1f} units under perfect review '
      f'or {replay["break_even_raw_to_verified"]["break_even_avoided_loss_configured_review"]:.1f} units under the default '
      "review error assumption.", "small")
    p("See decision_cost/evaluation.py, decision_cost/models.py and decision_cost/scenario.json.", "small")


    new_page("05 / EVIDENCE RECOVERY", "Completing citations reduced referrals")
    p("Our audit found that some withheld claims had the right answer and an incomplete list of supporting "
      "observations. We added a recovery step that fills only the missing citations for an eligible claim "
      "and sends the copy back through the source, arithmetic and dependency checks.")
    table(["Check", "Cases", "Result"], [
        ["Historical claim replay", "36 scheduled model runs",
         f'{recovered_formal["total_recovered_correct"]} correct claims restored. '
         f'{recovered_formal["direct_attribution_recoveries"]} had missing citations and '
         f'{recovered_formal["cascade_dependency_recoveries"]} depended on those claims.'],
        ["Fault checks and controls", "56 cases that should be withheld and 34 clean controls",
         f'{fault["total_fault_false_accepts"]} false accepts. All 34 clean controls passed.'],
    ], [122, 164, WIDTH-286])
    original_reviews = replay["baseline_decision_counts"]["deferred"]
    recovered_reviews = replay["recovered_decision_counts"]["deferred"]
    p(f"On the same {historical['decisions']} historical decisions as Table 1, recovery reduced referrals from "
      f"{original_reviews} to {recovered_reviews}. Both verified paths had zero observed automatic errors.", "small")
    p("The paired comparison on three new windows", "h2")
    rows = []
    for day, result in summaries.items():
        base, rec = (result["arms"][a] for a in arms)
        rows.append([day, base["cases"], pair(base["to_human"], rec["to_human"]),
                     pair(base["auto_error"], rec["auto_error"]),
                     pair(base["published_correct_claims"], rec["published_correct_claims"])])
    rows.append(["<b>Total</b>", n_cases, refs, pair(*(totals[a]["auto_error"] for a in arms)), pubs])
    table(["Window", "Paired decisions", "Referrals", "Automatic errors", "Correct claims published"], rows,
          [102, 82, 83, 90, WIDTH-357])
    p("<b>Table 5.</b> Each pair compares verification alone with verification plus recovery on the same "
      "saved report. Each window has 18 decisions (three tasks, two prompts, three repetitions). "
      "One report can contain multiple claims.", "caption")
    p("False alerts and correct automatic alerts", "h2")
    neg = alert["by_window"][jdate]["baseline"]
    ap = alert["pooled_decision_counts"]
    p(f"Both paths produced <b>{neg['false_positives']}/{neg['reference_no_alert']}</b> false concentration "
      "alerts. The six negatives are repetitions of a single June task, and none went to review. "
      "October and January contain no reference negatives, so neither has a measurable false-positive rate. "
      f"Of the 12 reference alerts in those two windows, correct automatic alerts rose from "
      f"{ap['baseline']['true_positives']} to {ap['recovery']['true_positives']}.")
    p("The June archive retains 11 successful first attempts and seven reports produced after retries. "
      "Both paths used this same archive. Results and traces are in audit/holdout_validation/ "
      "and audit/evidence_recovery/results/.", "small")


    new_page("06 / MEASURED RESOURCES", "API charges, review time and latency")
    p("Evidence recovery reuses the saved model reports and runs locally. We measured platform charges "
      "for generating those reports, time spent on two human reviews and the extra time to complete and verify citations.")
    table(["Resource", "Recorded result", "Measurement scope"], [
        ["DeepSeek charges", f"<b>CNY {money}</b> for the three held-out windows. Recovery used no extra model calls.",
         f'The billing export matches {billing["provider_usage"]["request_count"]} requests with reported usage. '
         f'{billing["requests_without_reported_usage"]} failed requests had no reported token count.'],
        ["Human review", f'<b>{minutes}</b> across two correct reviews '
         f'({elapsed(recorded[0]["seconds"])} and {elapsed(recorded[1]["seconds"])}).',
         "Both cases were referrals from the June verification-only path."],
        ["Local verification", f"<b>+{t1} ms</b> in October and <b>+{t2} ms</b> in January, "
         "median added wall time per report.",
         f'{sum(x["pairs"] for x in timing_rows)} paired measurements across {timing["rounds"]} timed rounds. '
         "The timer covers local verification and publication."],
    ], [102, 190, WIDTH-292])
    p("<b>Table 6.</b> Both paths reuse the same billed collection. The two observed reviews have no assigned "
      "labor rate, so we report minutes and do not estimate realized net savings.", "caption")
    p("What the results can support", "h2")
    p("The study covers one pool, three types of research decisions and one report-generating model. "
      "Repetitions share event data and give limited evidence about unseen events. The held-out comparison "
      "evaluates citation recovery. "
      "The routing policies were evaluated on the two development events.")
    p("The project measures whether an archived report supports the prescribed research decision or "
      "quoted output. Testing financial impact would require observations of execution, gas, fills and returns.")
    p("The billing, review and timing records are billing-reconciliation.json, human-timing-observed.json "
      "and timing-summary.json in audit/holdout_validation/.", "small")


    new_page("07 / REPRODUCIBILITY", "Reproduce the results offline")
    p("The repository includes the saved reports, task definitions and reference decisions. "
      "The commands below rerun the checks against those records without model or RPC credentials.")
    table(["Finding", "Inputs and results", "Check from repository root"], [
        ["Historical reproducible coverage and shared arithmetic errors",
         "baseline/experiments/formal_v1/result_summary.json; baseline/audit/arithmetic_faults/",
         "Commands 1 and 2"],
        ["Original decision replay", HISTORICAL.as_posix(), "Command 3"],
        ["Six historical routing policies",
         "decision_cost/scenario.json; decision_cost/results/policy-summary.csv",
         "Command 4"],
        ["Recovery and fault cases",
         "audit/evidence_recovery/results/",
         "Command 5"],
        ["Three-window decisions and alert statistics",
         "audit/holdout_validation/ (dated decisions.csv, runs.json, alert-metrics.json)",
         "Commands 6 and 7"],
        ["Measured billing, reviewer time and verification time",
         "audit/holdout_validation/ (billing-reconciliation.json, human-timing-observed.json, timing-summary.json)",
         "Inspect the reconciled evidence files"],
    ], [122, 252, WIDTH-374])
    p("Offline commands", "h2")
    p("Install decision_cost/requirements.txt for the historical cost and recovery scripts. "
      "Copy the block into a shell from the repository root. The first line creates a temporary directory. "
      "The seven commands then follow in the order listed above.", "small")
    commands = (
        'run_dir="$(mktemp -d)"',
        '(cd baseline && python3 -B -m curve_audit replay)',
        '(cd baseline && python3 -B audit/arithmetic_faults/replay_publication_guard.py .)',
        '(cd baseline && python3 -B audit/decision_replay_20261112/replay.py .)',
        'python3 -B decision_cost/evaluation.py --output "$run_dir/cost"',
        'python3 -B audit/evidence_recovery/run_all.py --output "$run_dir/recovery"',
        'python3 -B audit/holdout_validation/replay.py',
        'python3 -B audit/holdout_validation/alert_metrics.py \\',
        '    audit/holdout_validation "$run_dir/alerts.json"',
    )
    flow.append(Preformatted("\n".join(commands), STYLES["code"]))
    p("Compare the generated alerts.json with audit/holdout_validation/alert-metrics.json. "
      "To add an event, collect both sources, freeze its tasks and schedule, and obtain human reference "
      "reviews before running the model. See docs/CONFIRMATION.md.", "small")

    new_page("APPENDIX / TECHNICAL NOTES", "Verification criteria and statistical limits")
    p("What counts as reproducible?", "h2")
    p("To count as correct and reproducible, a claim must match the specified query, value, unit "
      "and time. It must also name the required evidence IDs, dataset digest and formula version. "
      "A peak refers to the sampled hours. A peak between observations remains unknown.")
    n_template = formal["template"]["reproducible_correct"]
    p(f"The deterministic template reproduced all {n_template} answerable components without a model call. "
      "It establishes what the computation can produce from the frozen evidence.", "small")
    p("Recovery eligibility", "h2")
    p("The verifier must return incomplete_attribution, with the query, value and unit already "
      "matching the recorded calculation. The claim must use the bound dataset digest and formula version "
      "and name a nonempty strict subset of the required evidence. Recovery logs the added IDs on a copy "
      "of the report. Any claim that still fails a check or has an unresolved dependency remains withheld.")
    p("Stress tests and uncertainty", "h2")
    p("The fault suite includes 32 frozen cases, 18 shared-arithmetic cases and 40 extension cases. "
      "Of the 90 scripted checks, 34 were clean controls and 56 were cases to withhold.")
    p("Four policies each had zero observed errors in 11 automatic historical concentration decisions. "
      "The one-sided 95% upper limits are 23.84% (Clopper-Pearson) and 15.69% (Jeffreys). A uniform Beta "
      "prior gives a posterior mean of 7.69% and an upper limit of 22.09%. These calculations assume "
      "independence, although runs within each of the two events share evidence. The holdout false-positive "
      "rate uses only reference negatives as its denominator.")
    p("Replacing the default 5% high-risk error assumption with 7.69%, 15.69% or 23.84% raised the "
      "unrecovered rule from 0.65 to 1.46 average scenario units. With recovery, the rule averaged "
      "0.71 and full verification averaged 0.78 under the same three assumptions. "
      "These are sensitivity comparisons under specified assumptions.", "small")
    p("What the historical quote test measured", "h2")
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
