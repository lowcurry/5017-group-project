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
    return f"{a} -> {b}"


class Workflow(Flowable):
    """Draw a static vector flowchart with a separate evidence recovery branch."""

    def __init__(self):
        super().__init__()
        self.width = WIDTH
        self.height = 320

    def draw(self):
        c = self.canv
        # Both paths start with the same claims and rejoin at publication.
        self._arrow(c, 246, 276, 246, 264)
        self._arrow(c, 246, 230, 246, 218)
        c.setStrokeColor(TEAL)
        c.setLineWidth(1)
        c.line(246, 181, 246, 171)
        c.line(119, 171, 372, 171)
        self._arrow(c, 119, 171, 119, 157)
        self._arrow(c, 372, 171, 372, 157)
        c.line(119, 119, 119, 110)
        c.line(372, 119, 372, 110)
        c.line(119, 110, 372, 110)
        self._arrow(c, 246, 110, 246, 100)
        self._arrow(c, 246, 62, 246, 46)
        self._node(c, 113, 276, 266, 34, "1  Record two sources at fixed blocks",
                   "Store raw balances, quotes, units and block IDs")
        self._node(c, 113, 230, 266, 34, "2  Generate claims",
                   "Record each query, value, unit and evidence IDs")
        self._node(c, 113, 181, 266, 37, "3  Verify claims and evidence scope",
                   "Dual-source checks (V2) + exact-fraction balance math")
        self._node(c, 9, 119, 220, 38, "4A  Baseline: verify original report",
                   "Check source agreement, arithmetic and dependencies")
        self._node(c, 262, 119, 220, 38, "4B  Recovery: complete missing citations",
                   "On incomplete_attribution, add IDs and re-verify", accented=True)
        self._node(c, 94, 62, 304, 38, "5  Publish or withhold each claim",
                   "Publish only claims that pass all required checks")
        self._node(c, 94, 8, 304, 38, "6  Use the verified claim or refer the decision",
                   "Refer to a reviewer if a required claim is withheld")

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
    scenario = read_json(root, Path("decision_cost/scenario.json"))
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
    money_exact = f'{Decimal(billing["recorded_charge"]):.8f}'
    money_short = f'{Decimal(billing["recorded_charge"]):.2f}'
    recorded = human["observations"]
    minutes = elapsed(sum(x["seconds"] for x in recorded))
    timing_rows = list(timing["events"].values())
    t1, t2 = (f'{x["median_incremental_wall_ms"]:.2f}' for x in timing_rows)
    g, e = (f'{groups[k]["reproducible_correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))
    g_pct = 100 * groups["G"]["reproducible_correct"] / groups["G"]["answerable"]
    e_pct = 100 * groups["E"]["reproducible_correct"] / groups["E"]["answerable"]
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

    def link(url, label):
        return f'<a href="{url}" color="#08736E"><u>{label}</u></a>'

    def new_page(kicker, title):
        flow.append(PageBreak())
        section(kicker, title)

    p("I. EXECUTIVE SUMMARY &amp; PROBLEM STATEMENT", "kicker")
    p(TITLE, "title")
    p("MSBD 5017  /  GROUP 08 &nbsp;&nbsp; | &nbsp;&nbsp; " + edition, "small")
    p("WANG Hanyang, HE Yuequn, WU Yuxuan", "small")
    p("Curve 3pool is an Ethereum liquidity pool for swapping DAI, USDC and USDT. When one stablecoin "
      "faces selling pressure, pool balances shift and swap quotes depend on trade size: across 50 "
      "historical blocks, scaling up a small quote overestimated the size-matched contract quote. A risk "
      "analyst needs the exact block, all three coin balances and a size-matched quote; picking the wrong "
      "peak-imbalance hour directs the follow-up query to the wrong block.", "lead")
    p("We built an auditable investigation pipeline to test whether an AI agent can produce numerical "
      "claims that an analyst can independently reproduce under fixed tasks, data, model, tools and "
      "budget. Starting from historical blocks in the March 2023 USDC depeg, the agent binds each claim "
      "to raw evidence and formulas, while a separate verifier checks structured numerical claims across "
      "two RPC captures—with an extra exact-fraction check on covered balance and imbalance metrics—before publication.")
    p("What the evaluation found", "h2")
    card(f"<b>Evidence guidance improved reproducibility.</b> Across 48 scheduled answer slots under "
         f"identical tasks, data, model and budget, the ordinary prompt produced {g} ({g_pct:.1f}%) "
         f"correct and reproducible answers, versus {e} ({e_pct:.1f}%) with evidence guidance.")
    card(f"<b>A fixed risk-cost rule matched the lowest modeled cost.</b> Across {historical['decisions']} "
         f"historical decisions, the rule and logistic regression averaged "
         f"{float(policies['rule']['average_cost']):.2f} scenario cost units per decision (lower is better), "
         f"outperforming both tabular neural encoders ({float(policies['tuned_encoder']['average_cost']):.2f}).")
    card(f"<b>Completing missing citations cut human referrals.</b> Several withheld claims were numerically "
         f"correct but omitted required evidence IDs. Deterministic citation recovery and re-verification "
         f"cut referrals from {totals['baseline']['to_human']} to {totals['recovery']['to_human']} across "
         f"{n_cases} paired decisions in three held-out windows, with zero observed automatic errors.")
    p("Member contributions", "h2")
    contributions = [
        ["<b>WANG Hanyang</b>", "Project proposal; first-version verification; revision proposals and collaborative edits after the second version; second-version verification; final presentation."],
        ["<b>HE Yuequn</b>", "Code implementation for the first and second versions; first-version review; second-version verification; presentation slide deck preparation."],
        ["<b>WU Yuxuan</b>", "First-version verification; interim presentation."],
    ]
    contrib_table = Table(
        [[Paragraph(name, STYLES["small"]), Paragraph(role, STYLES["small"])] for name, role in contributions],
        colWidths=[102, WIDTH - 102],
        hAlign="LEFT",
    )
    contrib_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
    ]))
    flow.append(contrib_table)


    new_page("II. BUSINESS &amp; MARKET ANALYSIS", "Who needs a second check on a liquidity alert?")
    p("The immediate users are DeFi risk analysts and on-chain researchers who sign off on incident "
      "reports under time pressure. Their gating decision is concrete: can the team publish a reported "
      "peak-imbalance hour and size-specific swap quote as-is, or must a colleague reconstruct the math?", "lead")
    p("Sizing a first group of potential users", "h2")
    p("On 27 November 2026, Morpho's public dashboard listed <b>41 curators</b> [1], and its vault directory "
      "identified at least <b>five distinct curators</b> managing USDC or USDT vaults: Steakhouse Financial, "
      "Gauntlet, Sentora, Galaxy Curation and Keyrock [2]. These five teams serve as a pilot screening "
      "list rather than a full market-size estimate; broader demand across DeFi risk teams and willingness "
      "to pay remain unmeasured and would be tested through direct outreach and pilot pricing.")
    p("How an analyst checks a reported peak", "h2")
    table(["Available approach", "What the analyst gets", "What still needs checking"], [
        ["Public analytics query", "A shared query such as Dune's Curve3Pool Balance Data [3].",
         "Which block interval, token units and trade size support the exact claim in the report?"],
        ["Fixed local script", "Deterministic reproduction of pre-coded queries without model calls.",
         "Engineers must hard-code each new question and maintain checks whenever schemas change."],
        ["This workflow", "A generated claim bound to evidence IDs and a replayable publication verdict.",
         "A human reviewer handles only missing or conflicting evidence that verification cannot resolve."],
    ], [110, 185, WIDTH-295])
    p("<b>Table 1.</b> Three ways to audit a liquidity alert. Fixed-script benchmark coverage is evaluated in Section III.", "caption")
    p("Go-to-market: a shadow pilot with one risk team", "h2")
    p("Run a low-friction shadow pilot by replaying a past liquidity alert alongside one risk team's "
      "existing workflow. The analyst confirms the selected peak hour and size-matched quote, then logs "
      "review minutes, referrals and incorrect releases on subsequent alerts to test whether the workflow "
      "saves checking time without increasing errors. Willingness to pay would be evaluated separately "
      "through customer interviews or pilot pricing quotes.")
    p("Sources accessed 27 November 2026. [1] " + link("https://data.morpho.org/curation", "Morpho, Curation")
      + ". [2] " + link("https://app.morpho.org/vaults", "Morpho, Vaults") + ". [3] "
      + link("https://dune.com/queries/2148264", "Dune, Curve3Pool Balance Data") + ".", "caption")


    new_page("III. TECHNICAL SOLUTIONS &amp; DESIGN  /  ORIGINAL DESIGN AND BASELINE", "From initial design to the first measured system")
    p("Our initial design targeted the March 2023 USDC depeg: an agent that traces every Curve 3pool "
      "balance and swap-quote claim to exact Ethereum blocks and formulas, paired with an off-chain "
      "verifier and a local smart contract recording the evidence manifest hash. A deterministic "
      "template, an ordinary prompt (G) and an evidence-guided prompt (E) run on identical tasks and data.")
    p("To test robustness beyond a single crisis, the first measured system expanded the benchmark to "
      "two historical events (November 2022 FTX and March 2023 USDC). Our primary verifier (<b>V1</b>) "
      "recomputes each claim from the primary archived RPC capture; the dual-source verifier (<b>V2</b>) "
      "cross-checks a second archived RPC capture before publication. Table 2 summarizes the baseline replay.")
    p("The same reports, with and without a publication check", "h2")
    p("We replayed the generated reports across three operational research decisions: labeling the "
      "direction of USDC share change, selecting a follow-up investigation hour, and deciding whether "
      "to trigger a concentration alert.")
    table(["Decision input", "Correct automatic", "Wrong automatic", "Sent to review"], [
        [label, historical[key]["correct"], historical[key]["wrong"], historical[key]["deferred"]]
        for key, label in (("raw", "Available model output"), ("verified", "Claims published by V2"))
    ], [WIDTH-258, 86, 86, 86])
    p(f"<b>Table 2.</b> Both paths used the same {historical['decisions']} scheduled decisions from the "
      "November 2022 FTX and March 2023 USDC windows. Each row sums to 36. "
      "The replay logged referrals without measuring review time or reviewer outcomes.", "caption")
    p("V2 verification blocked an erroneous automatic label of falling USDC share when recorded balances "
      f"actually rose. However, strict gating sent {historical['correct_sent_to_review']} otherwise "
      "correct decisions to human review because their citations omitted part of the required observation "
      "window. Applying uniform verification to every case motivated both the risk-cost routing study "
      "and deterministic citation recovery.")
    p("Reproducibility archives and offline replay commands are indexed in Table 9 and Appendix A.", "small")


    new_page("III. TECHNICAL SOLUTIONS &amp; DESIGN  /  BENCHMARK", "A fair test of independent verification")
    p("Four core questions guided our benchmark design: shared arithmetic can mask identical bugs, "
      "scoring only submitted answers rewards omissions, and uniform checks waste analyst effort on "
      "low-stakes decisions.")
    table(["Evaluation risk / design question", "What we implemented", "How we tested it"], [
        ["Can two checks share the same mistake?",
         "Cross-check a second RPC capture (V2) and recompute covered balance metrics with separate exact-fraction arithmetic.",
         "Inject shared calculation errors and replay matched clean controls."],
        ["Can a system score well by answering less?",
         "Fix required answer slots and token budgets; score omissions and refusals against checked references.",
         "Compare prompts on identical scheduled tasks and completed pairs."],
        ["When do the stakes justify a review?",
         "Compare direct use, verification and human referral under explicit error-loss and review-cost assumptions.",
         "Replay six routing policies on the 36 historical decisions (Table 5)."],
        ["Can a small model route cases more cheaply?",
         "Compare a fixed rule, logistic regression and two tabular encoder training setups.",
         "Cross-validate by holding out an entire historical event at a time (Table 5)."],
    ], [132, 192, WIDTH-324])
    p("<b>Table 3.</b> Rows 1-2 address written proposal feedback; rows 3-4 address the instructor's "
      "follow-up suggestions on intervention timing and lightweight routing.", "caption")
    p("Scoring every scheduled answer slot", "h2")
    p("The historical benchmark spans six tasks and 51 hourly snapshots across both events. Prompts G "
      "and E share the same DeepSeek Flash model (deepseek-flash), tools, evidence and budget ceilings.", "small")
    table(["Outcome", "Ordinary prompt (G)", "Evidence guidance (E)"], [
        ["Reports submitted / scheduled", *(f'{groups[k]["successful_submissions"]}/{groups[k]["scheduled_runs"]}' for k in ("G", "E"))],
        ["Correct before verification", *(f'{groups[k]["correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))],
        ["Correct and reproducible", g, e],
        ["Correct and published", *(f'{groups[k]["published_correct"]}/{groups[k]["answerable"]}' for k in ("G", "E"))],
    ], [WIDTH-205, 101, 104])
    p("<b>Table 4.</b> Each prompt had 48 scheduled answer slots (16 answerable slots repeated three times), "
      "including slots from G's two runs that hit the budget ceiling. Across the 16 completed pairs "
      "(43 answerable slots in those runs), both prompts got 40/43 right; G made 34/43 reproducible "
      "versus 39/43 for E.", "caption")
    p("<b>Reference answers.</b> Because the historical benchmark used a consolidated workbook, original "
      "reviewer independence is unverified; in each held-out window, two human reviews agreed with "
      "dual-source recomputation on all nine reference items.", "small")
    p("<b>Shared arithmetic.</b> All six injected errors passed when V1 and V2 shared the faulty formula. "
      "The separate exact-fraction balance check withheld all six errors and retained all six clean controls; "
      "V2 continues to verify swap quotes.", "small")


    new_page("III. TECHNICAL SOLUTIONS &amp; DESIGN  /  SYSTEM ARCHITECTURE", "From recorded blocks to verified claims")
    p("Curve 3pool holds DAI, USDC and USDT on Ethereum. For each target hour we archive the block "
      "identifier, all three token balances and size-specific quote queries from two RPC providers "
      "(Alchemy and dRPC). Off-chain, V2 checks structured numerical claims across both sources, "
      "separate exact-fraction arithmetic checks covered balance-share and imbalance metrics (while V2 "
      "continues to verify swap quotes), and publication enforces evidence-scope completeness.")
    flow.append(Workflow())
    p("<b>Figure 1.</b> Both evaluation paths process the same saved reports. Baseline verification (4A) "
      "withholds claims with missing evidence IDs; recovery (4B) triggers only on eligible "
      "incomplete_attribution claims, completes missing citations on a copy and re-runs all checks.", "caption")
    p("<b>On-chain registry contract.</b> In the local chain demo, the EvidenceRegistry smart contract "
      "exposes <b>register</b> to anchor an evidence manifest hash and URI on-chain, along with "
      "<b>exists</b> and <b>lookup</b> so any auditor can verify registration and match the stored hash "
      "against the archived package. Off-chain source and arithmetic checks establish whether the "
      "numerical claims themselves reproduce.")
    p("Contract source and local deployment receipt: baseline/contracts/anchor.mjs and baseline/results/current_chain/anchor.json.", "small")


    new_page("III. TECHNICAL SOLUTIONS &amp; DESIGN  /  RISK AND COST", "When is verification worth the cost?")
    p("Following the instructor's suggestions to weigh error loss against human review cost and test "
      "lightweight routing models (including final-layer tuning), we replayed the 36 historical decisions "
      "from Table 2 against two fixed baselines: using available model output directly, or verifying every case.")
    p("Each routing policy estimates pre-verification error risk—using task type in the fixed rule, or "
      "pre-verification evidence fields in the learned models—and picks the lowest expected-cost action:")
    p("&bull; <b>Direct use:</b> estimated error probability times the decision's error loss.<br/>"
      "&bull; <b>Verification:</b> checking cost, expected human-referral cost, and residual post-check error loss.<br/>"
      "&bull; <b>Human review:</b> analyst review time plus expected loss from errors that survive review.", "small")
    p("For example, the fixed rule assigns a 2% error risk and 15-unit loss to follow-up hour selection "
      "(0.30 expected units for direct use), whereas a missed concentration alert carries a 200-unit loss, "
      "justifying verification even at low error probability.", "small")
    p("Does training a lightweight model lower that cost?", "h2")
    p("We compared logistic regression, a frozen tabular encoder with a trained classification head, and "
      "an encoder with its final layer and head tuned jointly. Each learned policy trained on one event "
      "and was evaluated on the other, keeping repeated runs from the same event grouped together.")
    labels = (
        ("raw", "Use available model output (baseline)"), ("verified", "Verify every case (baseline)"),
        ("rule", "Fixed risk-cost rule"), ("logistic", "Logistic regression"),
        ("frozen_head", "Frozen encoder, trained head"),
        ("tuned_encoder", "Final encoder layer and head tuned"),
    )
    table(["Policy", "Human referrals", "Automatic errors", "Cost / decision"], [
        [label, r["to_human"], r["auto_error"], f'{float(r["average_cost"]):.2f}']
        for key, label in labels for r in (policies[key],)
    ], [WIDTH-247, 82, 82, 83])
    p("<b>Table 5.</b> Referrals and automatic errors from the 36-decision replay (costs in modeled "
      "scenario units). Defaults: 5 min per review at 1 unit/min, 0.2 units per verification, 2% residual "
      "review error rate, 200 units per missed concentration alert, and 40 units per false alert. Routing "
      f'assumes {scenario["verification_referral_probability"]:.0%} of verified cases go to review and '
      f'{scenario["verification_fraction_errors_missed"]:.0%} of direct-use error loss remains after verification.',
      "caption")
    p("The fixed rule and logistic regression each referred two cases (0.65 units/decision), while both "
      "neural variants referred four (1.18 units/decision); none produced an observed automatic error. "
      "Tuning the final encoder layer did not reduce modeled cost across these two events.")
    p("Under these assumptions, verifying every case breaks even against direct use once avoided error loss "
      f'reaches {replay["break_even_raw_to_verified"]["break_even_avoided_loss_perfect_review"]:.1f} units '
      f'(perfect review) or {replay["break_even_raw_to_verified"]["break_even_avoided_loss_configured_review"]:.1f} '
      "units (default 2% review error). Code and scenario parameters are indexed in Table 9.", "small")


    new_page("III. TECHNICAL SOLUTIONS &amp; DESIGN  /  EVIDENCE RECOVERY", "Completing citations reduced referrals")
    p("Auditing withheld claims revealed that many had the right numerical answer but cited only a subset "
      "of the required observations. We added a deterministic recovery step that fills missing citations "
      "only for eligible claims and sends the updated copy back through all source, arithmetic and "
      "dependency checks.")
    p("Worked example: a correct peak with incomplete evidence", "h2")
    p(f"On {jdate}, report H02_G_1 identified the correct sampled peak at <b>{peak_hour} UTC</b> "
      f"but cited only that single hour. Verifying a window maximum requires all {len(recovered_ids)} "
      "eligible hourly observations, so the verifier withheld the peak and its dependent next-hour quote, "
      "referring the follow-up decision to a human reviewer.")
    p("Recovery populated the missing evidence IDs on a copy of the report and re-ran full verification. "
      f"Both the peak and the {quote_hour} UTC quote of <b>{Decimal(quote['value']):.6f} USDT per USDC</b> "
      "(for a 100,000 USDC input) passed, allowing the system to select the follow-up hour automatically.")
    table(["Check", "Cases", "Result"], [
        ["Historical claim replay", "36 scheduled model runs",
         f'{recovered_formal["total_recovered_correct"]} correct claims restored '
         f'({recovered_formal["direct_attribution_recoveries"]} with missing citations and '
         f'{recovered_formal["cascade_dependency_recoveries"]} dependent claims).'],
        ["Fault checks and controls", "56 cases to withhold and 34 clean controls",
         f'{fault["total_fault_false_accepts"]} false accepts; all 34 clean controls passed.'],
    ], [122, 164, WIDTH-286])
    p("<b>Table 6.</b> Historical claim recovery and scripted fault-injection safety checks.", "caption")
    original_reviews = replay["baseline_decision_counts"]["deferred"]
    recovered_reviews = replay["recovered_decision_counts"]["deferred"]
    p(f"On the same {historical['decisions']} historical decisions from Table 2, recovery cut referrals from "
      f"{original_reviews} to {recovered_reviews} with zero observed automatic errors in either path.", "small")
    p("Paired comparison across three held-out windows", "h2")
    rows = []
    for day, result in summaries.items():
        base, rec = (result["arms"][a] for a in arms)
        rows.append([day, base["cases"], pair(base["to_human"], rec["to_human"]),
                     pair(base["auto_error"], rec["auto_error"]),
                     pair(base["published_correct_claims"], rec["published_correct_claims"])])
    rows.append(["<b>Total</b>", n_cases, refs, pair(*(totals[a]["auto_error"] for a in arms)), pubs])
    table(["Window", "Paired decisions", "Referrals", "Automatic errors", "Correct claims published"], rows,
          [102, 82, 83, 90, WIDTH-357])
    p("<b>Table 7.</b> Paired comparison of verification alone vs. verification plus recovery (base -> +recovery) "
      "on the same saved reports. Each window has 18 decisions (3 tasks x 2 prompts x 3 repetitions); "
      "one report can contain multiple claims.", "caption")
    p("False alerts and correct automatic alerts", "h2")
    neg = alert["by_window"][jdate]["baseline"]
    ap = alert["pooled_decision_counts"]
    p(f"Both paths produced <b>{neg['false_positives']}/{neg['reference_no_alert']}</b> false concentration "
      "alerts in the June window (six repetitions of the sole negative reference task, with zero referrals). "
      "Because October and January contained only true-alert reference cases, false-positive rates are "
      f"unmeasured in those two windows; among their 12 reference alerts, correct automatic alerts rose "
      f"from {ap['baseline']['true_positives']} to {ap['recovery']['true_positives']}.")


    new_page("III. TECHNICAL SOLUTIONS &amp; DESIGN  /  RESOURCES AND LIMITS", "API charges, review time and latency")
    p("Evidence recovery operates locally on archived reports with zero additional LLM calls. Table 8 "
      "separates measured model billing, observed human review time and local verification latency.")
    p("The June archive includes 11 reports from first attempts and 7 produced after retries; both "
      "publication paths evaluated the exact same saved reports.", "small")
    table(["Resource", "Recorded result", "Measurement scope"], [
        ["DeepSeek API charges", f"<b>CNY {money_short}</b> (exact: {money_exact}) across all three held-out windows; 0 extra calls for recovery.",
         f'Billing export matches {billing["provider_usage"]["request_count"]} requests with usage; '
         f'{billing["requests_without_reported_usage"]} failed requests reported no tokens.'],
        ["Human review time", f'<b>{minutes}</b> across two correct reviews '
         f'({elapsed(recorded[0]["seconds"])} and {elapsed(recorded[1]["seconds"])}).',
         "Both cases were referrals from the June verification-only path."],
        ["Local verification latency", f"<b>+{t1} ms</b> (Oct) and <b>+{t2} ms</b> (Jan), "
         "median added wall time per report.",
         f'{sum(x["pairs"] for x in timing_rows)} paired measurements across {timing["rounds"]} timed rounds '
         "(covering local verification and publication)."],
    ], [108, 188, WIDTH-296])
    p("<b>Table 8.</b> Both paths reuse the same billed reports. Because observed reviews carry no assumed "
      "labor rate, minutes are reported directly without estimating net dollar savings.", "caption")
    p("Limitations and scope of evidence", "h2")
    p("This study covers one liquidity pool (Curve 3pool), three research decision types and one "
      "report-generating model. Repeated runs share underlying event snapshots, limiting generalization "
      "to unseen market regimes. Held-out testing validates citation recovery, whereas routing policies "
      "were evaluated on the two development events. Finally, the benchmark verifies whether archived "
      "reports support prescribed decisions and quotes; measuring live trading impact would require "
      "execution, gas, fill and return data.")
    p("Future work", "h2")
    p("Freeze the routing policy and test it on new market events—including multiple independent "
      "non-alert windows to bound false-positive rates—then benchmark it side-by-side against an "
      "analyst's existing workflow, logging review minutes and release errors directly.")


    p("IV. Tokenomics and Incentives", "h2")
    p("<b>Intentional no-token design.</b> This system deliberately issues no native token. Institutional "
      "risk teams typically prefer predictable operating costs (API usage and compute) over token-price "
      "volatility, staking lockups or gas overhead for routine off-chain checks. Instead, the intended "
      "operational incentive—to be validated in a live pilot—is testing whether verifiable reports can "
      "reduce analyst re-verification time and erroneous-release risk, while optional on-chain hash "
      "anchoring via EvidenceRegistry supports tamper-evident audit trails on existing infrastructure.")

    new_page("V. SOURCE CODE", "Run the archived investigation")
    p("Public repository: " + link("https://github.com/lowcurry/5017-group-project",
      "https://github.com/lowcurry/5017-group-project") + ".", "body")
    p("The shortest verification requires Python 3.10 or later. From the repository root, run:", "body")
    flow.append(Preformatted("python3 -B audit/holdout_validation/replay.py", STYLES["code"]))
    p("The script prints one line per window ending in <b>18 paired publications reproduced</b>. "
      "It rebuilds both publication paths for all 54 archived runs and checks them against saved outputs.")
    p("Build and test the local contract", "h2")
    p("In a working copy of baseline/, install locked dependencies with pnpm install --frozen-lockfile --ignore-scripts. "
      "Run pnpm run anchor to compile and deploy EvidenceRegistry to the in-memory chain, register the "
      "manifest hash and read it back (printing PASS and the new receipt path). Run pnpm run test:contract "
      "to verify receipt-writing behavior. Full setup commands are documented in the root README.", "small")
    p("Evidence behind the reported results", "h2")

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
    p("<b>Table 9.</b> Archived results and the offline commands that reproduce them.", "caption")
    new_page("APPENDIX A  /  REPRODUCTION AND DEFINITIONS", "Offline commands and verification criteria")

    p("Install decision_cost/requirements.txt for the historical cost and recovery scripts. "
      "Run the block below from the repository root; the first line creates a temporary directory, "
      "and the seven checks follow the order in Table 9.", "small")
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
    p("Compare alerts.json with audit/holdout_validation/alert-metrics.json. The procedure for adding a "
      "new validation window is in docs/CONFIRMATION.md.", "small")


    p("What counts as reproducible?", "h2")
    p("To count as correct and reproducible, a claim must match the specified query, value, unit "
      "and time, and name the required evidence IDs, dataset digest and formula version. "
      "A peak refers strictly to sampled hours; any peak between hourly observations remains unknown.")
    n_template = formal["template"]["reproducible_correct"]
    p(f"The deterministic template reproduced all {n_template} answerable components without a model call.", "small")
    p("Recovery eligibility", "h2")
    p("The verifier must return incomplete_attribution, with the query, value and unit already "
      "matching the recorded calculation. The claim must use the bound dataset digest and formula version "
      "and cite a nonempty strict subset of the required evidence. Recovery logs the added IDs on a copy "
      "of the report; any claim that still fails a check or has an unresolved dependency remains withheld.")
    p("Stress tests and uncertainty", "h2")
    p("The fault suite includes 32 frozen cases, 18 shared-arithmetic cases and 40 extension cases. "
      "Of the 90 scripted checks, 34 were clean controls and 56 were cases to withhold.")
    p("Four policies each had zero observed errors in 11 automatic historical concentration decisions. "
      "The one-sided 95% upper limits are 23.84% (Clopper-Pearson) and 15.69% (Jeffreys). A uniform Beta "
      "prior gives a posterior mean of 7.69% and an upper limit of 22.09%. These calculations assume "
      "independence, although runs within each event share evidence. The holdout false-positive "
      "rate uses only reference negatives as its denominator.")
    p("Raising the default 5% high-risk error assumption to 7.69%, 15.69% or 23.84% raised the "
      "unrecovered rule from 0.65 to 1.46 average scenario units. With recovery, the rule averaged "
      "0.71 and full verification averaged 0.78 under the same three assumptions.", "small")
    p("What the historical quote test measured", "h2")
    p("Across all 50 frozen blocks tested in the quote replay (out of the 51 hourly snapshots), scaling "
      "up a small swap quote overestimated the size-matched output. Both quotes gave the same eligibility "
      "decision at the five thresholds tested. See baseline/REPORT_ERRATA.md.", "small")
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
    parser.add_argument("--edition", default="Technical report | 27 November 2026")
    opts = parser.parse_args()
    build(opts.repo, opts.output or opts.repo / "MSBD5017_Final_Project_Report.pdf", opts.edition)
