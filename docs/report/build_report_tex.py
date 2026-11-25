"""
Build the LaTeX version of the project report from the files in results/.

    python docs/report/build_report_tex.py        (from the DMP folder)

It reuses the helpers of build_report.py (same numbers as the HTML report), fills
report_template.tex, writes docs/report/latex/CompuOpti_report.tex and copies the figures to
docs/report/latex/figures/. Every number and table comes from the result files; the only values
typed by hand are the formulation-comparison timings of the template (measured once, section 6.6).

Compile the latex/ folder with pdfLaTeX (e.g. upload latex/CompuOpti_report_overleaf.zip to Overleaf:
New project > Upload project), or locally with `latexmk -pdf CompuOpti_report.tex` if TeX is installed.
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_report as br  # noqa: E402  (puts the repository root on sys.path too)
from src.io_parser import ProblemInstance  # noqa: E402

ROOT, RES = br.ROOT, br.RES
OUT = HERE / "latex"
FIG = OUT / "figures"
f_int, front, load_json, read_csv = br.f_int, br.front, br.load_json, br.read_csv

FIGURES = {
    "toy_objective_space.png": RES / "toy" / "objective_space.png",
    "toy_schedule_compromise.png": RES / "toy" / "schedule_compromise.png",
    "toy_schedule_max_profit.png": RES / "toy" / "schedule_max_profit.png",
    "medium_heatmap.png": ROOT / "docs" / "slides" / "heatmap_medium.png",
    "medium_objective_space.png": RES / "medium" / "objective_space.png",
    "medium_schedule_compromise.png": RES / "medium" / "schedule_compromise.png",
    "large_certificate.png": ROOT / "docs" / "slides" / "certificate_large.png",
    "large_heatmap.png": ROOT / "docs" / "slides" / "heatmap_large.png",
    "large_objective_space.png": RES / "large_merged" / "objective_space.png",
    "benchmark.png": RES / "benchmark.png",
}


# ------------------------------------------------------------------ LaTeX helpers
def esc(s) -> str:
    """Escape the LaTeX special characters that can appear in values read from the results."""
    s = str(s)
    for a, b in [("&", r"\&"), ("%", r"\%"), ("#", r"\#"), ("_", r"\_"), ("$", r"\$")]:
        s = s.replace(a, b)
    return s


def num(x, nd=0) -> str:
    return f"{x:,.{nd}f}"


def pct(x, nd=1) -> str:
    return f"{x:.{nd}f}\\%"


def tabular(colspec, headers, rows, size=r"\small") -> str:
    out = [size, r"\begin{tabular}{" + colspec + "}", r"\toprule", " & ".join(headers) + r" \\", r"\midrule"]
    out += [" & ".join(str(c) for c in r) + r" \\" for r in rows]
    out += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(out)


def table_float(colspec, headers, rows, caption, label, size=r"\small") -> str:
    return ("\\begin{table}[htbp]\n\\centering\n\\caption{" + caption + "}\\label{" + label + "}\n"
            + tabular(colspec, headers, rows, size) + "\n\\end{table}\n")


def best_under(rows, k=None, e=None) -> float:
    """Best profit on the front with f2 <= k and f3 <= e (exact on an exact front)."""
    return max([float(r["profit"]) for r in rows
                if (k is None or f_int(r, "max_projects") <= k)
                and (e is None or f_int(r, "max_duration") <= e)] + [0.0])


def price_rows(rows, best_profit, key):
    vals = sorted({f_int(r, key) for r in rows if f_int(r, key) > 0}, reverse=True)
    lines = [(cap, best_under(rows, **({"e": cap} if key == "max_duration" else {"k": cap})))
             for cap in vals]
    lines = [(c, b, 100 * (best_profit - b) / best_profit) for c, b in lines]
    first_max = min(c for c, b, _ in lines if b == best_profit) if any(
        b == best_profit for _, b, _ in lines) else vals[0]
    lines = [l for l in lines if l[0] <= first_max]
    return [[(f"$\\ge {c}$" if c == first_max else f"{c}"), num(b), pct(l)] for c, b, l in lines]


def price_block(rows, best_profit, exact, name, label) -> str:
    """Two side-by-side tables: price of short projects and price of focus."""
    head = "Best profit" if exact else "Best found"
    parts = []
    for key, title, what in [("max_duration", "Longest project $\\le$", "short projects"),
                             ("max_projects", "Projects / engineer $\\le$", "focus")]:
        cap = (f"{name}: price of {what}" + ("" if exact else " (profits found, i.e. lower bounds)") + ".")
        parts.append("\\begin{minipage}[t]{0.48\\linewidth}\\centering\n\\caption{" + cap + "}"
                     + ("\\label{" + label + "-" + ("dur" if key == "max_duration" else "foc") + "}\n")
                     + tabular("rrr", [title, head, "Loss"], price_rows(rows, best_profit, key),
                               size=r"\footnotesize")
                     + "\n\\end{minipage}")
    return "\\begin{table}[htbp]\n\\centering\n" + "\\hfill\n".join(parts) + "\n\\end{table}\n"


def key_rows(rows, rec_id, name, label) -> str:
    best = max(rows, key=lambda r: (float(r["profit"]), -f_int(r, "max_projects") - f_int(r, "max_duration")))
    rec = rows[rec_id]
    body = [["Maximum profit", num(float(best["profit"])), best["max_projects"], best["max_duration"],
             best["late_projects"], best["n_completed"]],
            [f"Tchebychev compromise (\\#{rec_id})", num(float(rec["profit"])), rec["max_projects"],
             rec["max_duration"], rec["late_projects"], rec["n_completed"]]]
    return table_float("lrrrrr", ["", "Profit $f_1$", "$f_2$", "$f_3$", "Late $f_4$", "Projects done"], body,
                       f"{name}: two reference points of the front. $f_2$: largest number of projects per "
                       f"engineer; $f_3$: longest project (days).", label)


def id_ranges(ids) -> str:
    """[6, 7, 8, 11] -> '\\#6--\\#8 and \\#11'."""
    ids = sorted(ids)
    groups, start = [], None
    for i, v in enumerate(ids):
        if start is None:
            start = v
        if i == len(ids) - 1 or ids[i + 1] != v + 1:
            groups.append(f"\\#{start}" if start == v else f"\\#{start}--\\#{v}")
            start = None
    return groups[0] if len(groups) == 1 else ", ".join(groups[:-1]) + " and " + groups[-1]


# ------------------------------------------------------------------ main
def main():
    V = {}
    names = [l.strip() for l in (HERE / "group.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    V["AUTHORS"] = " \\\\[4pt]\n".join(esc(n) for n in names)
    V["DATE"] = "November 2026"

    # ---------------- instances
    inst_rows, ratio = br.instance_rows()
    V["INSTANCE_TABLE"] = table_float(
        "lrrrrrrrrr",
        ["Instance", "$H$", "$|S|$", "$|J|$", "$|Q|$", "Demand", "Capacity", "Dem./cap.",
         "$x$ created / $|S||J||Q|H$", "Variables / constraints"],
        [[esc(c) for c in r] for r in inst_rows],
        "Instances. Demand: person-days required by all projects. Capacity: working days of all engineers "
        "(leave excluded), ignoring skills. Last two columns: size of the final model.",
        "tab:instances", size=r"\footnotesize\setlength{\tabcolsep}{4pt}")
    undo = [100 * (1 - r[6] / r[5]) for r in inst_rows]
    V["UNDOABLE_RANGE"] = f"{min(undo):.0f}--{max(undo):.0f}\\%"
    V["STEP_VS_BIGM_VARS"] = f"about {100 * ratio:.0f}\\%"
    loc = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in
              list((ROOT / "src").rglob("*.py")) + [ROOT / f for f in
                                                    ["run_pareto.py", "benchmark.py", "certify_front.py"]])
    V["LOC"] = f"{round(loc, -2):,}"

    # ---------------- toy
    toy, toy_s = front("toy"), load_json(RES / "toy" / "summary.json")
    toy_json = load_json(RES / "toy" / "pareto_front.json")
    V["TOY_N"], V["TOY_SOLVES"], V["TOY_REC"] = toy_s["n_points"], toy_s["n_mip_solves"], toy_s["recommended_id"]
    V["TOY_UNSUP"] = toy_s["n_points"] - toy_s["n_supported"]
    unsup_ids = [int(r["id"]) for r in toy if r["supported"] != "True"]
    V["TOY_UNSUP_IDS"] = id_ranges(unsup_ids)
    assert max(float(r["profit"]) for r in toy) == 65, "toy text assumes a maximum profit of 65"
    assert any(float(r["profit"]) == 55 and f_int(r, "max_projects") <= 2 and f_int(r, "max_duration") <= 2
               for r in toy), "toy text assumes a point (55, <=2, <=2)"
    V["TOY_TABLE"] = table_float(
        "rrrrr>{\\raggedright\\arraybackslash}p{4.4cm}l",
        ["\\#", "Profit $f_1$", "$f_2$", "$f_3$", "Late $f_4$", "Completed projects", "Type"],
        [[r["id"], num(float(r["profit"])), r["max_projects"], r["max_duration"], r["late_projects"],
          esc(br.jobs_label(p["completed_jobs"])),
          ("supported" if r["supported"] == "True" else "\\textbf{unsupported}")
          + (", compromise" if int(r["id"]) == toy_s["recommended_id"] else "")]
         for r, p in zip(toy, toy_json)],
        "Toy: the complete non-dominated set for $(f_1, f_2, f_3)$; late projects ($f_4$) reported. "
        "$f_2$: largest number of projects per engineer; $f_3$: longest project (days).",
        "tab:toy")

    # ---------------- medium
    med, med_s = front("medium"), load_json(RES / "medium" / "summary.json")
    rts = [json.loads(l)["runtime"] for l in
           (RES / "medium" / "checkpoint.jsonl").read_text(encoding="utf-8").splitlines()]
    V.update(MED_N=med_s["n_points"], MED_SUP=med_s["n_supported"],
             MED_UNSUP=med_s["n_points"] - med_s["n_supported"], MED_SOLVES=med_s["n_mip_solves"],
             MED_MIN=f"{med_s['runtime_sec'] / 60:.0f}", MED_MEDIAN=f"{statistics.median(rts):.0f}",
             MED_SLOWEST=f"{max(rts):.0f}", MED_REC=med_s["recommended_id"])
    assert med_s["exact"], "the medium text says the front is complete"
    V["MED_KEYROWS"] = key_rows(med, med_s["recommended_id"], "Medium", "tab:med-key")
    best_med = max(float(r["profit"]) for r in med)
    V["MED_PRICE"] = price_block(med, best_med, True, "Medium", "tab:med-price")
    lost = lambda v: 100 * (best_med - v) / best_med  # noqa: E731
    e5, k4 = best_under(med, e=5), best_under(med, k=4)
    both, k5e6 = best_under(med, k=4, e=5), best_under(med, k=5, e=6)
    e3, k1 = best_under(med, e=3), best_under(med, k=1)
    rec = med_s["recommended"]
    V["MED_CEO_TEXT"] = (
        f"Each limit \\emph{{alone}} is cheap down to a point: projects of at most 5 days cost "
        f"{best_med - e5:.0f} of {best_med:.0f} ({pct(lost(e5))}), and engineers on at most 4 projects cost "
        f"{best_med - k4:.0f} ({pct(lost(k4))}) (Tables~\\ref{{tab:med-price-dur}} and~\\ref{{tab:med-price-foc}}). "
        f"Below that, losses grow fast: 3-day projects cost {pct(lost(e3), 0)} of the profit and one project per "
        f"engineer {pct(lost(k1), 0)}. But \\textbf{{limits interact}}: imposing both cheap limits at once costs "
        f"{best_med - both:.0f} (profit {both:.0f}, $-${pct(lost(both))}), whereas projects of at most 6 days with at "
        f"most 5 projects per engineer still earn {k5e6:.0f} ($-${pct(lost(k5e6))}). Figure~\\ref{{fig:heat-medium}} "
        f"gives the optimal profit for every pair of limits: this table \\emph{{is}} the menu the decision maker "
        f"chooses from, and on an exact front every entry is a proven optimum.")
    V["MED_HEAT_CAPTION"] = (
        f"Medium: optimal profit for each pair of limits (max projects per engineer $\\le k$, longest project "
        f"$\\le e$ days), read off the exact front. Framed: the Tchebychev compromise ($k={rec['max_projects']}$, "
        f"$e={rec['max_duration']}$: {rec['profit']:.0f}) and a cheap combination ($k=5$, $e=6$: {k5e6:.0f}).")
    V["CONCL_MED_TEXT"] = (
        f"On medium, projects of at most 5 days alone or at most 4 projects per engineer alone cost "
        f"{pct(lost(e5))} and {pct(lost(k4))} of the maximum profit, but imposing both costs {pct(lost(both))}, "
        f"while projects of at most 6 days with at most 5 projects per engineer together cost {pct(lost(k5e6))}.")
    print(f"medium: best {best_med:.0f}, e<=5 {e5:.0f}, k<=4 {k4:.0f}, both {both:.0f}, k5e6 {k5e6:.0f}, "
          f"e<=3 {e3:.0f}, k<=1 {k1:.0f}, compromise {rec}")

    # ---------------- large
    lm, lm_s = front("large_merged"), load_json(RES / "large_merged" / "summary.json")
    best_large = max(float(r["profit"]) for r in lm)
    V.update(LARGE_N=lm_s["n_points"], LARGE_MAXP=num(best_large))
    a_n, a_p = br.checkpoint_stats(RES / "large" / "checkpoint.jsonl")
    b_n, b_p = br.checkpoint_stats(RES / "large_sequential" / "checkpoint.jsonl")
    sa, sb = load_json(RES / "large" / "summary.json"), load_json(RES / "large_sequential" / "summary.json")
    runs = [["A: one weighted MIP per point, 60~s per solve, step jump", sa["n_points"], a_n, a_p,
             f"{sa['runtime_sec'] / 3600:.1f}~h (budget)"],
            ["B: profit, then $f_3$, then $f_2$ in sequence, 60~s per stage", sb["n_points"], b_n, b_p,
             f"{sb['runtime_sec'] / 3600:.1f}~h (budget)"]]
    cert_dir = RES / "large_certify"
    cs = load_json(cert_dir / "summary.json")
    cert = read_csv(cert_dir / "certificate.csv")
    recs = [json.loads(l) for l in (cert_dir / "certificate.jsonl").read_text(encoding="utf-8").splitlines()]
    lm_json = load_json(RES / "large_merged" / "pareto_front.json")
    kept_c = sum(1 for p in lm_json if p["status"] == "CERTIFY")
    c_proven = sum(1 for r in recs if r.get("status") == "OPTIMAL")
    runs.append(["C: certificate, 120~s per solve", f"{cs['n_points']} new, {kept_c} kept", cs["n_mip_solves"],
                 c_proven, f"{cs['runtime_sec'] / 3600:.1f}~h"])
    runs.append(["\\textbf{Merged} (non-dominated union, re-checked)", f"\\textbf{{{lm_s['n_points']}}}",
                 lm_s["n_mip_solves"], "", f"{lm_s['runtime_sec'] / 3600:.1f}~h"])
    V["LARGE_RUNS"] = table_float(
        ">{\\raggedright\\arraybackslash}p{5.2cm}rrrr", ["Run", "Points", "Solves", "Proven optimal", "Time"], runs,
        "Large instance runs. For C: the schedules it found, and how many of them are on the merged front.",
        "tab:large-runs")

    bound = load_json(RES / "large" / "max_profit_bound.json")
    a_max = max(float(r["profit"]) for r in front("large"))
    b_max = max(float(r["profit"]) for r in front("large_sequential"))
    b_first = json.loads((RES / "large_sequential" / "checkpoint.jsonl").read_text(encoding="utf-8")
                         .splitlines()[0])
    b_first_profit = None
    if b_first.get("objectives"):
        b_first_profit = float(b_first["objectives"]["profit"])
    assert bound["best_profit"] == bound["upper_bound"] == best_large, "max profit should be proven"
    V["LARGE_BOUND_TEXT"] = (
        f"A separate profit-only solve proved that the maximum profit is \\textbf{{{bound['best_profit']:.0f}}} "
        f"in {bound['runtime_s']:.0f}~s. The weighted run (A) stopped at {a_max:.0f}: the combined objective is "
        f"much harder to \\emph{{prove}} than profit alone, which motivated the sequential mode (B), which found "
        f"{b_max:.0f}" + (" in its first solve." if b_first_profit == b_max else ".")
    )

    num_ = lambda v: float(v) if v not in ("", "None", None) else None  # noqa: E731
    gaps = [num_(r["gap_pct"]) for r in cert if num_(r["gap_pct"]) is not None]
    gaps_b = [num_(r["gap_before_pct"]) for r in cert if num_(r.get("gap_before_pct")) is not None]
    proven = sum(1 for g in gaps if g == 0)
    gains = [float(r["best_found"]) - float(r["profit_front"]) for r in cert]
    improved = sum(1 for g in gains if g > 0.5)
    n = len(cert)
    n_pir = sum(1 for r in cert if r["status"] == "PROVEN_IN_RUN")
    n_gub = sum(1 for r in cert if r["status"] == "PROVEN_GLOBAL_UB")
    buckets = [("0\\% (proven optimal)", lambda g: g == 0), ("(0, 1\\%]", lambda g: 0 < g <= 1),
               ("(1, 2\\%]", lambda g: 1 < g <= 2), ("(2, 5\\%]", lambda g: 2 < g <= 5),
               ("(5, 10\\%]", lambda g: 5 < g <= 10), ("(10, 20\\%]", lambda g: 10 < g <= 20),
               ("$>$ 20\\%", lambda g: g > 20)]
    brows = [[b, sum(1 for g in gaps_b if f(g)), sum(1 for g in gaps if f(g))] for b, f in buckets]
    med_gap, med_b = statistics.median(gaps), statistics.median(gaps_b)
    gub = cs["global_ub"]
    top = [num_(r["gap_pct"]) for r in cert if float(r["best_found"]) >= 0.9 * best_large]
    big = [r for r in cert if num_(r["gap_pct"]) > 20]
    big_txt = (f" All {len(big)} gaps above 20\\% have moderately tight limits "
               f"($f_2\\le {max(int(r['f2']) for r in big)}$, $f_3\\le {max(int(r['f3']) for r in big)}$), "
               f"where neither the schedules nor the bounds converged within 120~s." if big else "")
    key = lambda r: (float(r["profit"]), f_int(r, "max_projects"), f_int(r, "max_duration"))  # noqa: E731
    old_keys = {key(r) for r in front("large_merged_before_certify")}
    dominated_old = len(old_keys - {key(r) for r in lm})
    lvl_pts, lvl_exact = br.level_slice([r for r in recs if r["key"].startswith("level:")],
                                        ProblemInstance.from_json(ROOT / "data" / "large_instance.json").horizon,
                                        cs["time_per_solve"])
    n_lvl = sum(1 for r in recs if r["key"].startswith("level:"))
    lvl_txt = (f"\n\nRun C also swept the level $f_2\\le 1$ (one project per engineer), which runs A and B never "
               f"reached: {n_lvl} lexicographic solves. Each one proves the best profit for its duration limit, and "
               f"the next limit starts one day below the duration just found, so no limit is skipped: this slice of "
               f"the front is \\textbf{{exact}} ({len(lvl_pts)} points, best profit "
               f"{max(p for p, _, _ in lvl_pts):.0f}).\n" if lvl_exact and lvl_pts else "\n")
    extra = f" and {n_gub} already reach the proven maximum profit" if n_gub else ""
    V["LARGE_CERT"] = (
        f"\\paragraph{{Quality certificate (run C).}} For each of the {n} non-empty points $(p_i, a_i, b_i)$ of the "
        f"front of runs A and B we solved $\\max f_1$ subject to $f_2\\le a_i$ and $f_3\\le b_i$ for up to 120~s, "
        f"warm-started from the point ({n - n_pir - n_gub} solves; {n_pir} points were already proven optimal by "
        f"their own run{extra}). Gurobi returns the best schedule it finds, $\\mathrm{{best}}_i\\ge p_i$, and a proven "
        f"upper bound $UB_i$, capped at {gub:.0f} (the proven maximum profit, valid for every box). The best profit "
        f"achievable within these limits therefore lies in $[\\mathrm{{best}}_i, UB_i]$, and "
        f"$\\mathrm{{gap}}_i=(UB_i-\\mathrm{{best}}_i)/UB_i$ is a \\emph{{proven upper bound}} on the share of profit "
        f"our answer may miss there.\n\n"
        f"\\paragraph{{What it showed.}} The first approximation was weaker than it looked: the certificate found "
        f"\\textbf{{better schedules for {improved} of the {n} points}} (up to +{max(gains):.0f} profit), and "
        f"\\textbf{{{dominated_old} of the {len(old_keys)} points of A and B are dominated}} in the merged front. "
        f"After certification the median gap is \\textbf{{{pct(med_gap)}}} (it was {pct(med_b)}), {proven} of the "
        f"{n} boxes are proven optimal, and the worst gap is {pct(max(gaps))} (Table~\\ref{{tab:cert}} and "
        f"Figure~\\ref{{fig:cert}}). The gaps are smallest where a decision is most likely to be made: for the "
        f"{len(top)} boxes whose best profit is at least 90\\% of the maximum, the median gap is "
        f"{pct(statistics.median(top))} (largest {pct(max(top))}).{big_txt}\n\n"
        + table_float("lrr", ["Gap $(UB-\\text{profit})/UB$", "Boxes before C", "Boxes after C"], brows,
                      "Certified gaps, one box per point of A and B: the original point (before C) and the best "
                      "schedule found by C (after C), both measured against the bounds of C.", "tab:cert")
        + lvl_txt)
    V["CERT_SENTENCE"] = (  # short form for the summary; the details are in section 6.3
        f"the maximum profit ({num(best_large)}) and the best profit for {proven} of the {n} limit pairs checked are "
        f"proven optimal, and elsewhere our profit is at most {max(gaps):.0f}\\% below the best achievable (median "
        f"gap over all pairs {pct(med_gap)})")
    V["LIM_LARGE"] = (
        f"For {n - proven} of the {n} limit pairs our profit is not proven optimal: it may be up to {pct(max(gaps))} "
        f"below the best achievable within the same limits (median over all pairs {pct(med_gap)}), and trade-offs "
        f"that no run explored may be missing. It must be presented as ``the best trade-offs found, with certified "
        f"gaps'', not as the front.")
    V["LARGE_KEYROWS"] = key_rows(lm, lm_s["recommended_id"], "Large (merged front)", "tab:large-key")
    V["LARGE_PRICE"] = price_block(lm, best_large, False, "Large", "tab:large-price")

    # ---------------- f4 on toy
    f4_rows = []
    for d, sec in [("toy_max_projects_late_projects", "$f_1, f_2, f_4$"),
                   ("toy_max_duration_late_projects", "$f_1, f_3, f_4$")]:
        for r in front(d):
            f4_rows.append([sec, r["id"], num(float(r["profit"])), r["max_projects"], r["max_duration"],
                            r["late_projects"], "yes" if r["supported"] == "True" else "\\textbf{no}"])
    V["TOY_F4_TABLE"] = table_float("lrrrrrl", ["Criteria searched", "\\#", "Profit", "$f_2$", "$f_3$", "Late $f_4$",
                                                "Supported"], f4_rows,
                                    "Toy fronts with the late-projects criterion (both exact).", "tab:f4")

    # ---------------- benchmark
    bench = read_csv(RES / "benchmark.csv")
    V["BENCH_TABLE"] = table_float(
        "lrrrrl", ["Size $(H, |S|, |J|)$, seed", "Variables", "Max profit", "Proven in",
                   "Front: points / solves / time", "Exact?"],
        [[f"{r['H']}, {r['S']}, {r['J']} (seed {r['seed']})", f"{int(r['n_vars']):,}",
          f"{float(r['max_profit']):.0f}", f"{float(r['profit_time_s']):.2f}~s",
          f"{r['front_points']} / {r['front_solves']} / {float(r['front_time_s']):.0f}~s",
          "yes" if r["front_exact"] == "True" else "\\textbf{no}"] for r in bench],
        "Generated instances: maximum-profit solve and full front for each one.", "tab:bench",
        size=r"\footnotesize")
    all_proven = all(r["profit_optimal"] == "True" for r in bench)
    t_max = max(float(r["profit_time_s"]) for r in bench)
    small = [r for r in bench if int(r["n_vars"]) <= 1100]
    large_ = [r for r in bench if int(r["n_vars"]) >= 1500]
    n_fail = sum(1 for r in large_ if r["front_exact"] != "True")
    by_size = {}
    for r in bench:
        by_size.setdefault((r["H"], r["S"], r["J"]), []).append(float(r["front_time_s"]))
    spread = max(max(v) / min(v) for v in by_size.values() if len(v) > 1 and min(v) > 0)
    assert all(r["front_exact"] == "True" for r in small), "bench text: every small front exact"
    V["BENCH_TEXT"] = (
        ("Maximum profit alone stays easy: it is proven optimal on all "
         f"{len(bench)} instances, in at most {t_max:.0f}~s. " if all_proven else
         f"Maximum profit alone is proven in at most {t_max:.0f}~s where it is proven. ")
        + f"The full front is what gets hard: every front with up to about 1,000 variables was exact within "
          f"5 minutes, while from about 1,500 variables {n_fail} of {len(large_)} fronts exhausted the budget. "
          f"Two instances of the same size can differ by a factor of {spread:.0f} in time, so two seeds per size "
          f"show \\emph{{where}} it stops being easy, not a scaling law.")
    print(f"bench: max profit time {t_max}, big fronts failing {n_fail}/{len(large_)}, spread x{spread:.1f}")

    # ---------------- write
    tex = (HERE / "report_template.tex").read_text(encoding="utf-8")
    tex = "\n".join(l for l in tex.splitlines() if not l.startswith("%% TEMPLATE"))  # drop the template note
    for k, v in V.items():
        tex = tex.replace("@@" + k + "@@", str(v))
    assert "@@" not in tex, tex[tex.index("@@"):tex.index("@@") + 60]
    tex = ("% Generated by docs/report/build_report_tex.py from results/ -- edit report_template.tex instead.\n"
           + tex.lstrip() + "\n")
    bad = [(i, ch) for i, ch in enumerate(tex) if ord(ch) > 127]
    assert not bad, f"non-ASCII character in the LaTeX output: {bad[:5]}"
    OUT.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    (OUT / "CompuOpti_report.tex").write_text(tex, encoding="utf-8")
    for name, src in FIGURES.items():
        shutil.copy2(src, FIG / name)
    # optional school logo (docs/report/logo_centralesupelec.png/.jpg/.pdf); a placeholder is drawn without it
    logos = [p for p in (HERE / f"logo_centralesupelec.{ext}" for ext in ("png", "jpg", "pdf")) if p.exists()]
    for p in logos:
        shutil.copy2(p, FIG / p.name)
    zpath = OUT / "CompuOpti_report_overleaf.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(OUT / "CompuOpti_report.tex", "CompuOpti_report.tex")
        for name in list(FIGURES) + [p.name for p in logos]:
            z.write(FIG / name, f"figures/{name}")
    print("logo:", logos[0].name if logos else "none yet (placeholder box on the title page)")
    print("wrote", OUT / "CompuOpti_report.tex", "and", len(FIGURES), "figures;", "Overleaf zip:", zpath)


if __name__ == "__main__":
    main()
