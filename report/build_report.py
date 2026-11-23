"""Build the final report from the frozen Curve 3pool study."""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "MSBD5017_Final_Project_Report.pdf")
args = parser.parse_args()
def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


decision_replay = read_json(ROOT / "baseline/audit/decision_replay_20261112/results.json")
recovery_dir = ROOT / "audit/evidence_recovery/results"
holdout_dir = ROOT / "audit/holdout_validation"
historical_recovery = read_json(recovery_dir / "recovery_summary.json")
with (recovery_dir / "decision_cost_comparison.csv").open(encoding="utf-8-sig", newline="") as file:
    recovery_policies = list(csv.DictReader(file))
holdout_arms = []
for event in ("2023-06-15", "2023-10-23", "2024-01-11"):
    arms = read_json(holdout_dir / event / "summary.json")["arms"]
    holdout_arms.append((event, arms["baseline"], arms["recovery"]))
billing = read_json(holdout_dir / "billing-reconciliation.json")
timing = read_json(holdout_dir / "timing-summary.json")["events"]

INK = colors.HexColor("#17283B")
MUTED = colors.HexColor("#47586A")
TEAL = colors.HexColor("#087C78")
BLUE = colors.HexColor("#385D8A")
PALE = colors.HexColor("#F1F5F8")
RULE = colors.HexColor("#CDD9E0")
W, H = A4
M = 46
CW = W - 2 * M - 12


def style(name, font="Helvetica", size=10, leading=14.5, color=INK, **kwargs):
    return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading,
                          textColor=color, spaceAfter=6, alignment=TA_LEFT, **kwargs)


styles = {
    "kicker": style("kicker", "Helvetica-Bold", 10.5, 14, TEAL),
    "title": style("title", "Helvetica-Bold", 30, 33.5),
    "h1": style("h1", "Helvetica-Bold", 22, 26),
    "h2": style("h2", "Helvetica-Bold", 12.3, 17, TEAL, spaceBefore=6),
    "body": style("body", "Helvetica", 10.15, 14.25),
    "small": style("small", "Helvetica", 8.65, 12, MUTED),
    "reference": style("reference", "Helvetica", 8.65, 11.5, MUTED),
    "cell": style("cell", "Helvetica", 9.15, 12.0),
    "header": style("header", "Helvetica-Bold", 9.15, 12, colors.white),
    "code": style("code", "Courier", 8.1, 12, INK),
}
styles["reference"].spaceAfter = 3
flow = []


def p(text, kind="body"):
    flow.append(Paragraph(text, styles[kind]))


def h2(text):
    p(text, "h2")


def section(number, title):
    p(number, "kicker")
    p(title, "h1")
    flow.append(Spacer(1, 5))


def tab(header, rows, widths):
    data = [[Paragraph(text, styles["header"]) for text in header]]
    data.extend([[Paragraph(text, styles["cell"]) for text in row] for row in rows])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.extend([Spacer(1, 3), table, Spacer(1, 8)])


class CoverageChart(Flowable):
    def __init__(self):
        super().__init__()
        self.width = CW
        self.height = 170

    def draw(self):
        c = self.canv
        x0 = 165
        bar = 284
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        for i in range(5):
            x = x0 + i * bar / 4
            c.setStrokeColor(RULE)
            c.setLineWidth(0.5)
            c.line(x, 30, x, 151)
            c.drawCentredString(x, 17, f"{i*25}%")
        for y, label, num, fill in [
            (140, "G / raw correct", 40, BLUE), (122, "G / reproducible correct", 34, BLUE),
            (104, "G / published correct", 31, BLUE), (82, "E / raw correct", 45, TEAL),
            (64, "E / reproducible correct", 43, TEAL), (46, "E / published correct", 42, TEAL),
        ]:
            c.setFont("Helvetica", 9)
            c.setFillColor(INK)
            c.drawString(0, y-1, label)
            c.setFillColor(fill)
            c.roundRect(x0, y-3, bar * num / 48, 8, 2, stroke=0, fill=1)
            c.setFillColor(INK)
            c.drawString(x0 + bar * num / 48 + 5, y-1, f"{num}/48")


# Page 1
p("MSBD 5017  /  TRADE 2  /  FINAL PROJECT", "kicker")
flow.append(Spacer(1, 14))
p("Independently Verifiable Stablecoin Liquidity Investigation Agent", "title")
p("Evidence-grounded investigation and independent replay of Ethereum Curve 3pool")
p("WANG Hanyang &nbsp;&nbsp; HE Yuequn &nbsp;&nbsp; WU Yuxuan", "h2")
p("23 November 2026 | Revised final report", "small")
flow.append(Spacer(1, 17))
h2("Abstract")
p("This study tests whether evidence guidance and independent recomputation improve stablecoin investigation reports. We froze 51 block-pinned snapshots covering two Ethereum Curve 3pool windows and defined six tasks with 18 required components. Sixteen were answerable. We compared a deterministic template with ordinary and evidence-guided agents using the same DeepSeek Flash model, task data, tool access and budget limits.")
p("Across 18 scheduled runs per model condition, the ordinary agent achieved reproducible correct coverage of 34/48 (70.8%) and the evidence-guided agent achieved 43/48 (89.6%). Verification withheld six nonconforming structured numeric submissions, one with an incorrect value and five with incorrect query bounds or fields. Published correct coverage was 31/48 and 42/48, respectively. On a separate 32-case test, the second-source verifier rejected 10 of 12 injected faults and returned UNKNOWN on the other two.")
p("Across three held-out windows, evidence recovery reduced manual referrals from 12 to 3 over 54 paired decisions and increased correctly published claims from 120 to 132. Neither path made an observed automatic error. The six reference negatives for concentration alerts all came from one window and produced no false alerts. Recovery required no additional model calls and added about 3.8 ms of median local verification time per report in the two newer windows. [20]")
h2("Research questions")
p("<b>Generation.</b> Does evidence guidance change raw correctness, reproducibility or model usage under matched settings?")
p("<b>Verification.</b> Which claims survive a fixed verification and withdrawal rule, and how much correct coverage is lost?")
p("<b>Source retrieval.</b> What does a second archive interface add over independent arithmetic using the original snapshot?")
h2("Investigation workflow")
tab(["01 / Acquire", "02 / Generate", "03 / Recompute", "04 / Publish"], [[
    "Resolve blocks at UTC targets and store raw responses.",
    "Emit required answer slots, citations and a tool trace.",
    "Check original-source and second-source evidence separately.",
    "Retain PASS claims and record each withdrawal and reason."
]], [CW/4]*4)
p("The historical evidence package also includes a local-chain digest registration demonstration. [1, 2, 3]", "small")
flow.append(PageBreak())

# Page 2
section("01 / STUDY DESIGN", "Data and frozen benchmark")
p("The Ethereum Curve 3pool holds DAI, USDC and USDT. Nominal balances use the tokens' ERC-20 balanceOf(pool) values. Each target maps to a block whose timestamp is at or before the target and whose next block is later. The logs preserve target and observed times, block identities, request parameters and raw responses. The primary archive used Alchemy and the comparison archive used dRPC. The source check establishes agreement across those interfaces for the captured observations. Their upstream infrastructure is unverified. [2]")
tab(["Window and UTC sampling", "Snapshots", "Tasks"], [
    ["USDC depeg context, 10 Mar 2023 00:00 to 11 Mar 00:00 inclusive, plus 11 Mar 01:00 for the dependent quote", "26", "R01-R03"],
    ["FTX context, 9 Nov 2022 00:00 to 10 Nov 00:00 inclusive. The dependent quote falls inside this grid.", "25", "R04-R06"],
], [CW-130, 66, 64])
h2("Deterministic metrics")
p("Normalize each integer balance by dividing by 10 raised to its decimals. A token's share is its fraction of the three normalized balances. The share change is the final percentage minus the initial percentage. The quote rate is normalized output divided by input. Size sensitivity is the signed difference between the 1,000-input rate and the 100,000-input rate, divided by the 1,000-input rate and multiplied by 10,000. Imbalance is half the sum of the absolute differences between each share fraction and one third. [2]")
tab(["Task family", "Required components", "Boundary"], [
    ["R01 / R04", "DAI share, 1,000 USDC-to-USDT quote rate, and relative size sensitivity", "Fixed hour, direction and units"],
    ["R02 / R05", "USDC share change, sampled maximum imbalance, and quote one hour after the returned maximum", "Maximum over allowed grid points"],
    ["R03 / R06", "First-hour USDC share, largest allowed one-hour increase, and whether continuous peak is identifiable", "Increases checked from 18:00 to 00:00 UTC, skipping the excluded 22:00 point"],
], [80, 259, CW-339])
h2("Controls and denominator")
p("T is a single deterministic report per task. G and E each ran six tasks three times in a recorded randomized order. E adds requirements for precise citations, checks on units and times, and limits on interpretation. The comparison evaluates this prompt as a whole. The 16 answerable slots contribute 48 opportunities to each model arm, including failed runs. Each complete task set also has two non-answerable slots. [2, 3]")
p("The ten exploratory rounds found a contrast in citation replayability and no clear difference in unsupported statements on the local pilot model. Those observations informed the choice of reproducible correct coverage before the formal G/E freeze. The metric rewards correct answers with complete attribution, which E explicitly requests. We also report raw correctness and the descriptive prose audit. [3, 8, 10]", "small")
p("The freeze used a team-attested consolidated reference workbook with two calculation paths. It matched 18 components and 51 snapshots. Reviewer names, dates and signatures are absent from that workbook. The attestation records that independent dual review was unverified. Page 5 describes the post-freeze checks. [2, 4, 12]", "small")
flow.append(PageBreak())

# Page 3
section("02 / FORMAL EXPERIMENT", "Correct coverage and cost")
p("Correct coverage requires the correct value, unit, time and query for the frozen slot. Reproducible correct coverage also requires a V1 PASS, including the exact evidence ID set, formula version and dataset digest. Publication uses the original model draws and the same denominator. [3, 6]")
flow.append(CoverageChart())
tab(["Measure", "T template", "G ordinary", "E evidence"], [
    ["Scheduled runs / reports submitted", "6 / 6", "18 / 16", "18 / 18"],
    ["Correct coverage, raw", "16/16 (100.0%)", "40/48 (83.3%)", "45/48 (93.8%)"],
    ["Reproducible correct coverage, raw", "16/16 (100.0%)", "34/48 (70.8%)", "43/48 (89.6%)"],
    ["Correct coverage after publication", "N/A", "31/48 (64.6%)", "42/48 (87.5%)"],
    ["Withdrawn numeric items: value / query errors", "0 / 0", "1 / 2", "0 / 3"],
    ["Correct raw answers lost on publication", "N/A", "9/40 (22.5%)", "3/45 (6.7%)"],
    ["Over-refusals on answerable slots", "0/16", "0/48", "0/48"],
    ["Refusals passing the exact-reason check", "2/2", "0/6", "0/6"],
    ["Model calls / logged tokens", "0 / 0", "63 / 104,166", "70 / 129,007"],
], [CW-266, 83, 92, 91])
p("G's two stops omitted five answerable slots and one non-answerable slot. Across all 18 pairs, the reproducible gap was 18.75 percentage points. In the 16 pairs where both arms completed, raw correctness tied at 40/43, while reproducible correctness was 34/43 for G and 39/43 for E, a gap of 11.63 points. The completed-pair result describes that subset. [3, 14]", "small")
p("The frozen refusal scorer required the literal reason code. The prompt asked for a specific reason in words. By meaning, G gave five valid refusals across six scheduled non-answerable slots, with one omitted at a budget stop. E gave six valid refusals across six slots. [3, 8]", "small")
h2("Matched limits and actual use")
p("Client admission used the same ceilings of 48,000 input, 12,000 output and 60,000 total tokens, with 10 calls and 2,048 output tokens per call. All 133 provider receipts were within the reserved bounds. Archived generation wall times totaled 191.602 s for G and 234.587 s for E. [3, 11]")
p("Five offline batches recorded medians of 0.216 s for V1, 0.283 s for V2 and 0.002 s for in-memory publication. A separate replay measured a 0.901 s median from saved input loading through checked publication. Prose review and adjudication took seven person-hours. Live billing and total labor for the historical development study remain unavailable. Section 08 reports separate holdout billing and timing. [11, 13, 15]", "small")
flow.append(PageBreak())

# Page 4
section("03 / INDEPENDENT VERIFICATION", "Where the checks succeed and fail")
p("V1 recomputes claims using the frozen primary archive. V2 checks the allowed observations and block identities against a second RPC capture and applies the same verifier arithmetic. Both use arithmetic separate from the agent's calculation and check units, scope, evidence and numerical tolerance before publication. [5, 6]")
tab(["Case set", "Cases", "V1 PASS / FAIL / UNKNOWN", "V2 PASS / FAIL / UNKNOWN"], [
    ["Injected errors", "12", "5 / 7 / 0", "0 / 10 / 2"],
    ["Clean twins", "12", "12 / 0 / 0", "12 / 0 / 0"],
    ["Legitimate refusal scenarios", "4", "0 / 0 / 4", "0 / 0 / 4"],
    ["Missing secondary source", "2", "2 / 0 / 0", "0 / 0 / 2"],
    ["Unresolved source conflict", "2", "2 / 0 / 0", "0 / 0 / 2"],
], [165, 51, (CW-216)/2, (CW-216)/2])
p("V2 rejected 10/12 injected faults. The three additional detections involved block identity. The other two injected faults returned UNKNOWN because source evidence was missing. Both layers accepted all 12 clean twins. The design schedules 136 case or record checks across V1 and V2, including the two empty model records in both layers. This count is separate from claim verdicts and RPC traffic. [5]")
h2("Verification and withdrawal")
p("The captures agree on the bound fields, and V2 changed none of the 102 formal claim verdicts. Totals were 34 PASS, 3 FAIL and 11 UNKNOWN for G and 43 PASS, 2 FAIL and 9 UNKNOWN for E. Of the 12 correct claims withheld, eight had incomplete attribution and four depended on a withdrawn claim. The publisher constructs a new answer from surviving fields. The experiment measures structured withdrawals. [3, 5, 6]")
p("Correcting the slot-to-claim mapping restored three G and four E items. The corrected replay records UNKNOWN for source-conflict cases U3 and U4 and supersedes the original fault summary for those cases. The formal outcomes are unchanged by that correction. The archive retains the original reports and verdict records. [3, 5, 6]")
h2("Evidence integrity on a local chain")
p("The local Ganache demonstration registers the evidence manifest digest and reads it back. It rejects duplicate registration and produces a different, unregistered digest for a modified manifest. The saved receipt has chain ID 1337 and block number 2. Checking covered artifacts requires comparing their bytes with the manifest hashes. The arithmetic is checked by the separate verifier. [7]")
h2("Independent arithmetic before publication")
p("Across both windows, V1 and V2 accepted six controls and rejected six reports with injected denominator, token-decimal or extrema errors. With the same error injected into both verifiers, all six matching wrong reports originally passed and were published. The added exact-fraction check withheld all six and two quotes tied to erroneous peak times, while retaining all six controls. The check covers balance shares, changes and sampled imbalance maxima. Quote arithmetic continues under V2. [17]")
flow.append(PageBreak())

# Page 5
section("04 / INTERPRETATION", "What the evidence supports")
h2("Prose evidence audit")
p("Of the 42 scheduled records, 34 contained model prose. Six template stubs and two G budget stops sit outside the prose denominators. Two sets of annotations cover all 34 reports. He Yuequn confirmed the reconciliation workbook on 10 November 2026. Its fixed-component judgments match the frozen-data review. [8, 13]")
tab(["Prose evidence measure", "G ordinary", "E evidence"], [
    ["Numerical bundles matching frozen data", "42/43", "48/48"],
    ["Numerical bundles with incomplete original citations", "6/43", "2/48"],
    ["Supported scope-limited refusals observed", "5/5", "6/6"],
], [CW-160, 80, 80])
p("The two reviews contain 123 and 120 annotations. Claim counts differ in 19/34 reports. The team holds 62 reconciliation and explanation rows and a 143-unit follow-up packet. Common-list agreement and unsupported-claim rates are pending. A separate rule-based initial audit found at least one unsupported assertion in 11/16 G reports and 6/18 E reports. [8, 13, 15]", "small")
h2("A worked example")
p("For R02, the allowed endpoint snapshots put USDC at 29.679648% and 48.100138% of the pool's nominal balances. The difference is +18.420491 percentage points. The sampled maximum imbalance is 0.277696115 at 11 March 2023 00:00 UTC. The 100,000 USDC-to-USDT quote rate at the required following hour is 0.9897830738 USDT/USDC. The frozen packet identifies endpoint blocks 16794061 and 16801143. [2, 8]")
p("P41 reports a decrease of about 18.42 percentage points in both the structured item and the prose. Both verifiers reject the sign error, and the item is withheld. The endpoint balances show a share increase. Attributing that change to capital flows, solvency or a specific mechanism requires additional evidence. [2, 3, 8]")
h2("Reference checks received after the freeze")
p("WANG Hanyang (A) matched 18/18 components. WU Yuxuan (B) matched 17/18 after converting three proportions to percentages. The combined workbook includes A's formulas and saved results. The timing of B's access to A's work remains unverified. [12]", "small")
p("For R06/S2, B recorded 1.11692608956 percentage points. He Yuequn adjudicated the discrepancy on 9 November 2026 and accepted A's value of 1.16926089569553 percentage points, which matches the frozen reference used for formal scoring. The workbook preserves the original entries. The separate review record documents the decision. Review isolation remains unverified. [12]", "small")
h2("Scope of the findings")
p("Scheduled reproducible coverage was 15/24 versus 22/24 in the USDC window and 19/24 versus 21/24 in the FTX window, for G and E respectively. The gap was larger in the USDC window under both counting rules. This historical comparison covers one pool and six tasks with repeated runs. Its second-source results apply to the recorded interfaces and scripted faults. [2, 3, 5, 14]")
flow.append(PageBreak())

# Page 6
section("05 / PRACTICAL EVALUATION", "Historical decision replay")
h2("Research decisions from the same reports")
p("We applied three rules to archived reports before and after verification. The rules labelled net USDC share direction, selected the sampled peak hour and escalated eligible hourly share increases of at least 1 percentage point. Recomputing from the two matching frozen datasets yielded the same benchmark actions. Missing or withdrawn inputs went to review. All 36 scheduled decisions include the one affected by a budget stop. [16]")
research = decision_replay["research"]
research_rows = []
for key, label in (("share_direction", "Label direction"), ("peak_follow_up_hour", "Select follow-up hour"),
                   ("concentration_alert", "Flag concentration rise"), (None, "All scheduled decisions")):
    summary = research["overall"] if key is None else research["by_rule"][key]
    triplets = [" / ".join(str(summary[mode][result]) for result in ("correct", "wrong", "deferred"))
                for mode in ("raw", "verified")]
    research_rows.append([label, *triplets])
tab(["Research action", "Raw correct / wrong / review", "Verified correct / wrong / review"],
    research_rows, [145, 167, CW-312])
p("Verification caught the wrong decreasing label in R02 and sent nine additional decisions to review. Eight of the referrals involved correct actions with incomplete citations, including five peak-hour choices. Review time and eventual resolutions were outside the scope of the replay. [16]")
h2("Screening a 100,000 USDC-to-USDT quote")
p("At 50 historical hourly blocks we screened a hypothetical order using either 100 times the 1,000 USDC quote or the archived quote for the full order size. The rule accepted quoted output at or above the stated minimum. The two source datasets agree at each block. [16]")
quote = decision_replay["quote_screen"]
quote_rows = [[str(row["haircut_bps"]), f'{Decimal(row["floor_usdt"]):,.0f}',
               f'{row["proxy_eligible"]} / {row["size_matched_eligible"]}',
               f'{row["false_eligibility_count"] + row["missed_eligibility_count"]}/{row["snapshots"]}']
              for row in quote["overall"]]
tab(["Haircut, bps", "Min. output, USDT", "Proxy / size-matched eligible", "Decisions changed"],
    quote_rows, [88, 120, 167, CW-375])
error = quote["quote_error"]
largest = error["largest_absolute_error_case"]
p(f'The proxy overestimated output at all 50 blocks. The median gap was {error["median_absolute_error_usdt"]} USDT and the maximum was {largest["extrapolation_error_usdt"]} USDT at block {largest["block_number"]:,}. For that block, the output floor would have to be above {Decimal(largest["size_matched_output_usdt"]):,.6f} and at or below {Decimal(largest["extrapolated_output_usdt"]):,.6f} USDT to change the action. The five illustrated floors produced the same decisions. [16]')
p("This quote screen covers one pool, one trade direction and two historical stress windows. The quote screen compares block-pinned quotes and excludes fills, gas, transaction ordering and returns. The research actions are rule-based replays of repeated model outputs. [16]", "small")
flow.append(PageBreak())

# Intervention costs and risk routing
section("06 / INTERVENTION COST", "When to verify or refer")
p("We replayed six routing policies over the same 36 historical decisions. Direct release uses the available raw action. Full verification checks every case. The fixed-risk rule and learned policies choose release, verification or human review by minimizing expected cost. Their inputs are available before verification, including decision type, citation count and whether the report supplies a dataset hash and formula version. [18]")
p("The learned policies left one historical event out at a time, keeping its repeated runs together. The two neural policies use a tabular autoencoder. One trains only the classification head, while the other also tunes the last encoder layer. [18]")
h2("Default scenario on 36 scheduled decisions")
labels = {
    "raw": "Direct release",
    "verified": "Full verification",
    "rule": "Fixed-risk rule",
    "logistic": "Logistic regression",
    "frozen_head": "Frozen encoder + head",
    "tuned_encoder": "Last layer + head",
    "verified_recovered": "Full verification + recovery",
}
tab(["Policy", "Human referrals", "Automatic errors", "Units per decision"], [
    [labels[row["policy"]], str(row["to_human"]), str(row["auto_error"]), f'{float(row["average_cost"]):.2f}']
    for row in recovery_policies if row["policy"] in labels
], [CW-248, 81, 81, 86])
p("The default scenario assigns five minutes per referral, one cost unit per minute, 0.2 units per verification, and a 2% residual review error probability. The modeled losses are 100 units for a direction error, 15 for a follow-up hour error, 200 for a missed concentration alert, and 40 for a false alert. These parameters are illustrative. [18]")
h2("Break-even loss relative to direct release")
break_no = historical_recovery["decision_replay_36"]["break_even_raw_to_verified"]
break_yes = historical_recovery["decision_replay_36"]["break_even_raw_to_verified_recovered"]
tab(["Publication policy", "Perfect review", "2% residual review error"], [
    [label, f'{record["break_even_avoided_loss_perfect_review"]:.1f}', f'{record["break_even_avoided_loss_configured_review"]:.1f}']
    for label, record in (("Full verification", break_no), ("Full verification + recovery", break_yes))
], [CW-235, 105, 130])
p("Full verification blocked the one wrong automatic action, while sending nine more cases to review than direct release. Recovery resolved eight baseline referrals as correct automatic decisions. Under the default assumptions, the fixed-risk rule and logistic regression had lower average costs than the two neural variants. One observed error among 35 automatic releases gives limited evidence about their ranking in other events. [18, 19]", "small")
flow.append(PageBreak())

# Evidence recovery and small-sample bounds
section("07 / EVIDENCE RECOVERY", "Recover correct claims safely")
p("Some correct window-level answers cited only part of the required evidence. The recovery step completes deterministic citations and runs the second-source and independent publication checks again. It preserves the original model report and logs each added evidence ID. [19]")
h2("Conditions for recovery")
for line in [
    "1. Replaying the claim returns UNKNOWN with the reason <i>incomplete_attribution</i>.",
    "2. The claim's dataset hash matches the bound primary dataset.",
    "3. The formula version matches the frozen study version.",
    "4. The expected evidence set is nonempty.",
    "5. The original evidence is a nonempty strict subset of the expected evidence.",
    "6. The recovered copy passes V2, independent exact-fraction arithmetic where applicable, and task dependency checks.",
]:
    p(line, "small")
h2("Historical replay and fault controls")
summary = historical_recovery["formal_36_runs"]
tab(["Metric", "Full verification", "With recovery"], [
    ["G: correct published coverage", f'{summary["by_group"]["G"]["baseline_published_correct"]}/48',
     f'{summary["by_group"]["G"]["recovered_published_correct"]}/48'],
    ["E: correct published coverage", f'{summary["by_group"]["E"]["baseline_published_correct"]}/48',
     f'{summary["by_group"]["E"]["recovered_published_correct"]}/48'],
    ["Historical decisions referred", "10/36", "2/36"],
], [CW-225, 110, 115])
p("Of the 102 emitted structured claims, 12 correct claims had been withheld. Recovery restored all 12: eight by completing citations and four by restoring their dependencies. In the separate 90-case fault audit, none of the 56 non-clean cases was falsely accepted and all 34 clean controls were retained. [19]")
h2("Error bounds and risk stress test")
p("Four historical policies each recorded zero errors across 11 automatic concentration decisions. Under an independent-trial assumption, the one-sided 95% Clopper-Pearson upper bound is 23.84%. The Jeffreys posterior upper bound is 15.69%. The uniform Beta posterior has a mean of 7.69% and a one-sided 95% upper bound of 22.09%. The decisions come from only two events with repeated runs, so these bounds are nominal. [19]")
p("Using 7.69%, 15.69% or 23.84% instead of the default 5% high-risk probability raised the unrecovered rule's average scenario cost from 0.65 to 1.46. In those same stress scenarios, the recovered rule averaged 0.71 and full verification with recovery averaged 0.78. These are scenario results. [19]", "small")
flow.append(PageBreak())

# Held-out paired confirmation
section("08 / HELD-OUT CONFIRMATION", "Three windows and measured use")
p("Each held-out window used 26 hourly targets from Alchemy and dRPC. The two human reference reviews agreed with dual-source recomputation on all nine items in each window without adjudication. Each window scheduled 18 DeepSeek Flash reports across G and E. Seven first-window runs initially returned HTTP 402 and were retried, and the 11 successful original reports were retained. Both publication paths processed the same 54 completed reports. [20]")
h2("Paired decision outcomes")
tab(["Window", "Decisions", "Referrals", "Correct claims", "Auto. errors"], [
    [event, "18", f'{base["to_human"]} to {rec["to_human"]}',
     f'{base["published_correct_claims"]} to {rec["published_correct_claims"]}',
     f'{base["auto_error"]} to {rec["auto_error"]}']
    for event, base, rec in holdout_arms
] + [[ "<b>Total</b>", "<b>54</b>", "<b>12 to 3</b>", "<b>120 to 132</b>", "<b>0 to 0</b>"]],
    [111, 74, 93, 109, CW-387])
p("The frozen scenario total decreased from 98.90 to 36.40 cost units. The scenario applies assumed labor and error costs. The resource measurements below use their original units. [20]", "small")
h2("Concentration alerts")
p("Both paths issued 0 false alerts in 6 reference-negative decisions, and neither referred any of those decisions. All six were repeated runs of one task on 15 June 2023. The other two windows had no reference negatives, so their false-positive rates are undefined. In those two windows, correct automatic alerts increased from 8 of 12 to 12 of 12 and positive referrals fell from four to zero. These observations do not establish deployment error rates. [20]")
h2("Measured resources")
tab(["Resource", "Observed result"], [
    ["API billing", f'CNY {Decimal(billing["recorded_charge"]):.8f} for all three windows. The two newer windows added '
     f'CNY {Decimal(billing["two_new_windows_incremental_charge_cny"]):.8f}. Recovery made no additional model calls.'],
    ["Human review", "Two June cases took 6 min 48 s and 8 min 18 s, totaling 15 min 06 s. Both reviews were correct. These observed minutes apply only to those cases."],
    ["Local verification", f'The median paired overhead was {timing["holdout_20231023"]["median_incremental_wall_ms"]:.2f} ms in October '
     f'and {timing["holdout_20240111"]["median_incremental_wall_ms"]:.2f} ms in January across 540 interleaved pairs.'],
], [112, CW-112])
p("The platform export matches the local count of 211 requests with usage and all three token categories. Seven requests had no reported usage. We report measured API charges, human time and local verification time separately. [20]", "small")
flow.append(PageBreak())


section("09 / ENGINEERING IMPROVEMENTS", "How we acted on feedback")
p("The proposal review asked us to show that independent checks improve correctness without losing answer coverage, and to distinguish evidence integrity from factual verification. Later feedback from the professor asked us to account for the cost of human intervention and test whether a lightweight decision model or limited post-training would help. We translated these suggestions into engineering changes and evaluated each one separately. [1, 18-20]")
tab(["Feedback and implemented change", "What the evidence shows"], [
    ["<b>Separate calculation and source checks.</b> We bound the same hourly targets to two recorded RPC captures, checked matching block identities and raw data, and added a separate exact-fraction calculation for selected balance metrics before publication.",
     "A shared arithmetic fault could pass both V1 and V2. The extra publication check withheld all six injected shared-error cases and retained all six matching controls. [5, 6, 17]"],
    ["<b>Measure coverage and audit the references.</b> We fixed required answer slots and evidence, counted omissions and refusals separately, and compared G and E with the same tasks, model, tools and budget ceilings.",
     "Scheduled reproducible coverage was 34/48 for G and 43/48 for E. Both reviewers' reference answers in each of the three later windows agreed with dual-source recomputation on all nine items. [2-4, 14, 20]"],
    ["<b>Separate evidence integrity from correctness.</b> We recorded block and query details for replay, and tested evidence-digest registration independently of the publication checks.",
     "The local chain demo verified registration and retrieval of the manifest digest. The verifier separately checked source agreement and calculations. [2, 5-7]"],
    ["<b>Compare intervention costs by risk.</b> We priced release, verification and human referral under explicit assumptions, including a higher loss for missing a concentration alert than for issuing a false alert.",
     "On 36 historical decisions, the fixed rule and logistic regression both referred 2 cases and averaged 0.65 scenario units per decision. We report observed API charges and review times separately. [18, 20]"],
    ["<b>Test lightweight post-training.</b> We compared logistic regression with a frozen encoder plus a trained head and a model that also tunes the final encoder layer. We held out one historical event at a time.",
     "The two neural variants each averaged 1.18 scenario units per decision. Those variants offered no cost advantage over the simpler policies in this replay. [18]"],
], [CW*0.53, CW*0.47])
h2("A follow-up improvement from the error audit")
p("We traced part of the manual workload to correct claims with incomplete citations. We added deterministic evidence recovery followed by the same publication checks. On the same 54 saved reports, referrals fell from 12 to 3 and correct releases increased from 120 to 132. Neither path made an observed automatic error. The negative alert decisions came from repetitions in one window. [19, 20]")
flow.append(PageBreak())

section("10 / REPRODUCIBILITY", "Replay the archived evidence")
h2("Historical study commands from baseline/")
tab(["Action", "Command"], [
    ["Replay deterministic data and verification", "<font name='Courier'>python3 -B docs/reproduce_current.py</font>"],
    ["Check saved formal V1, V2 and publication outcomes", "<font name='Courier'>python3 -B -m curve_audit replay</font>"],
    ["Replay the 32 frozen fault cases", "<font name='Courier'>python3 -B docs/replay_formal_faults.py .</font>"],
    ["Replay research and quote-screening decisions", "<font name='Courier' size='8.3'>python3 -B audit/decision_replay_20261112/replay.py .</font>"],
    ["Check packaged file hashes", "<font name='Courier'>python3 -B docs/verify_delivery.py .</font>"],
    ["Run the Python test suite", "<font name='Courier'>python3 -B -m unittest discover -s tests -v</font>"],
], [199, CW-199])
p("Python 3.10 or later supports offline replay of the historical archive. The packaged code matches 25 of 31 freeze-time module hashes. Six revisions and one added arithmetic module are documented in docs/code_provenance.json. Formal replay checks archived inputs and outputs. The archive also contains the freeze-time sources. [2, 3, 5, 15]")
h2("Extended replay commands from the repository root")
p("Install decision_cost/requirements.txt for the cost and recovery audits. Replace each output path with a fresh location when rerunning. The three-window replay checks publication on saved model reports. The alert script recomputes statistics from archived decision tables. [18, 19, 20]", "small")
p("python3 -B decision_cost/evaluation.py --output /tmp/5017-cost", "code")
p("python3 -B audit/evidence_recovery/run_all.py --output /tmp/5017-recovery", "code")
p("python3 -B audit/holdout_validation/replay.py", "code")
p("python3 -B audit/holdout_validation/alert_metrics.py<br/>"
  "&nbsp;&nbsp;audit/holdout_validation /tmp/5017-alerts.json", "code")
p("Run the last command on one line. Compare the resulting JSON with audit/holdout_validation/alert-metrics.json. Preparing a new window requires new source captures, reference review and a freeze. The saved-report replay is entirely offline.", "small")
h2("Development optimization after the formal run")
p("The compact read-window response preserves raw balances, quotes and block identifiers. Three recorded budget stops passed a counterfactual pre-dispatch admission check with the smaller payload. In two nine-run development samples using the same tasks and budget, completed submissions rose from 8/9 to 9/9 and measured token use fell from 66,475 to 62,078. The samples used different model draws and the improvement is reported separately from the formal comparison. [9]")
flow.append(PageBreak())
section("11 / EVIDENCE INDEX", "Supporting records and scope")
p("Paths [2]-[17] refer to baseline/ in the public repository unless the file is a root document. [18]-[20] point to the extensions at the repository root.", "small")
for line in [
    "[1] Course Trade 2 scope and instructor proposal feedback, 14 October 2026.",
    "[2] results/current_preparation/{binding, dataset, secondary_dataset, tasks}.json and data/current_study/ source records.",
    "[3] experiments/formal_v1/{freeze, result_summary, publication_replay}.json and saved raw report traces.",
    "[4] results/current_preparation/reference_review/review_attestation.json and consolidated workbook.",
    "[5] experiments/formal_v1/verifier_replay_20261105.json, corrected 32-case fault replay.",
    "[6] curve_audit/current_verify.py and curve_audit/verification.py.",
    "[7] contracts/anchor.mjs and results/current_chain/anchor.json.",
    "[8] experiments/formal_v1/prose_review_input.json and audit/{prose_audit_summary, prose_audit_rows}.json.",
    "[9] results/optimization_20261105/{summary, budget_replay}.json.",
    "[10] references/trial-report-20261102.md, exploratory trial and metric selection rationale.",
    "[11] audit/RESOURCE_USE.md and audit/resource_use_20261109.json, recorded usage and packaged replay timings.",
    "[12] audit/reference_review_20261109/REVIEW.md, post-freeze reference review and evidence limits.",
    "[13] audit/prose_review_20261110/REVIEW.md, human review and reconciliation provenance.",
    "[14] audit/FORMAL_SENSITIVITY.md and audit/compare_run_sets.py, scheduled and completed-pair results.",
    "[15] README.md, LIMITATIONS.md, REPORT_ERRATA.md and docs/code_provenance.json.",
    "[16] audit/decision_replay_20261112/, historical decision replay.",
    "[17] audit/arithmetic_faults/README.md, commands and results for both arithmetic fault replays.",
    "[18] decision_cost/results/policy-summary.csv, diagnostics.json and scenario.json (under decision_cost/).",
    "[19] audit/evidence_recovery/results/ and audit/evidence_recovery/README.md, recovery, fault and risk tests.",
    "[20] audit/holdout_validation/RESULTS.md and its per-window decisions, alert metrics, billing and timing records.",
]:
    p(line, "reference")
h2("Scope across the study")
p("The benchmark covers one liquidity pool. The 54 held-out decisions come from three windows with repeated runs, and reference negatives for concentration alerts occur in just one window. The benchmark supports comparison of the observed release and referral outcomes. It does not estimate cross-event error rates or provide evidence of live fills, trading returns or monetized labor savings.")
p("Historical freeze SHA-256 &nbsp; <font name='Courier' size='8.2'>ec66a6aaf06a0a37eeff5e74bbda6e68a866be00617a00a9413087679e7cd220</font>", "small")


def footer(c, doc):
    c.saveState()
    c.setTitle("Independently Verifiable Stablecoin Liquidity Investigation Agent")
    c.setAuthor("WANG Hanyang, HE Yuequn, WU Yuxuan")
    c.setStrokeColor(RULE)
    c.setLineWidth(0.55)
    c.line(M, 39, W-M, 39)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(MUTED)
    c.drawString(M, 25, "MSBD 5017  /  TRADE 2  /  CURVE 3POOL")
    c.drawRightString(W-M, 25, str(doc.page))
    c.restoreState()


args.output.parent.mkdir(parents=True, exist_ok=True)
doc = SimpleDocTemplate(str(args.output), pagesize=A4, leftMargin=M, rightMargin=M,
                        topMargin=43, bottomMargin=50, invariant=True, title="Independently Verifiable Stablecoin Liquidity Investigation Agent")
doc.build(flow, onFirstPage=footer, onLaterPages=footer)
print(args.output)
