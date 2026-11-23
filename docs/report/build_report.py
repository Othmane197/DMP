"""
Build the project report (HTML + PDF) from the files in results/.
Every number and table in the report comes from the result files, nothing is typed by hand
(except the formulation-comparison timings of section 5.6, measured once during development).

    python docs/report/build_report.py            (from the DMP folder)

Group member names: one per line in docs/report/group.txt (optional).
PDF: printed with headless Microsoft Edge (or Chrome) if available.
"""

from __future__ import annotations

import csv
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
RES = ROOT / "results"

from src.io_parser import ProblemInstance  # noqa: E402
from src.models.gurobi_model import CompuOptiGurobiModel  # noqa: E402


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_csv(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fmt(x, nd=0):
    return f"{x:,.{nd}f}"


def table(headers, rows, caption=None, left=(0,)):
    h = "".join(f'<th class="{"l" if i in left else ""}">{c}</th>' for i, c in enumerate(headers))
    body = ""
    for r in rows:
        body += "<tr>" + "".join(f'<td class="{"l" if i in left else ""}">{c}</td>'
                                 for i, c in enumerate(r)) + "</tr>"
    cap = f"<caption>{caption}</caption>" if caption else ""
    return f"<table><tr>{h}</tr>{body}{cap}</table>"


def jobs_label(ids):
    return ", ".join(j.replace("Job", "J") for j in ids) if ids else "none"


# ------------------------------------------------------------------ instances
def instance_rows():
    rows, sizes = [], {}
    for name in ["toy", "medium", "large"]:
        inst = ProblemInstance.from_json(ROOT / "data" / f"{name}_instance.json")
        s = inst.summary()
        m = CompuOptiGurobiModel(inst, time_limit_sec=1)
        m.model.update()
        naive = len(inst.staff) * len(inst.jobs) * len(inst.qualifications) * inst.horizon
        sizes[name] = (m.model.NumVars, m.model.NumConstrs)
        demand, cap = s["total_workload_needed"], s["total_potential_capacity"]
        rows.append([name, inst.horizon, len(inst.staff), len(inst.jobs), len(inst.qualifications),
                     demand, cap, f"{100 * demand / cap:.0f}%",
                     f"{len(m.x):,} / {naive:,}", f"{m.model.NumVars:,} / {m.model.NumConstrs:,}"])
        m.model.dispose()
    # big-M vs switch size on medium
    inst = ProblemInstance.from_json(ROOT / "data" / "medium_instance.json")
    mb = CompuOptiGurobiModel(inst, time_limit_sec=1, formulation="bigm")
    mb.model.update()
    ratio = sizes["medium"][0] / mb.model.NumVars - 1
    mb.model.dispose()
    return rows, ratio


# ------------------------------------------------------------------ fronts
def front(name):
    return read_csv(RES / name / "pareto_front.csv")


def f_int(r, k):
    return int(float(r[k]))


def price_tables(rows, best_profit, exact, label):
    """best profit on the front under a cap on f3 (duration) or f2 (projects/engineer)."""
    note = "" if exact else " (found, i.e. lower bounds)"
    out = []
    for key, title in [("max_duration", "Longest project ≤"), ("max_projects", "Projects per engineer ≤")]:
        vals = sorted({f_int(r, key) for r in rows if f_int(r, key) > 0}, reverse=True)
        top = None
        lines = []
        for cap in vals:
            best = max(float(r["profit"]) for r in rows if f_int(r, key) <= cap)
            if top is None:
                top = cap
            loss = 100 * (best_profit - best) / best_profit
            lines.append((cap, best, loss))
        # keep the first cap reaching the max profit, then every smaller cap
        first_max = min(c for c, b, _ in lines if b == best_profit) if any(
            b == best_profit for _, b, _ in lines) else vals[0]
        lines = [l for l in lines if l[0] <= first_max]
        trs = [[(f"≥ {c}" if c == first_max else f"{c}"), fmt(b), f"{l:.1f}%"] for c, b, l in lines]
        out.append(table([title, "Best profit" + note, "Loss"], trs,
                         caption=f"{label}: price of {'short projects' if key == 'max_duration' else 'focus'}."))
    return out


def key_rows(rows, rec_id, label):
    best = max(rows, key=lambda r: (float(r["profit"]), -f_int(r, "max_projects") - f_int(r, "max_duration")))
    rec = rows[rec_id]
    hdr = ["", "Profit", "Max projects / engineer", "Longest project (days)", "Late projects", "Projects done"]
    tr = [["Max profit", fmt(float(best["profit"])), best["max_projects"], best["max_duration"],
           best["late_projects"], best["n_completed"]],
          [f"Tchebychev compromise (#{rec_id})", fmt(float(rec["profit"])), rec["max_projects"],
           rec["max_duration"], rec["late_projects"], rec["n_completed"]]]
    return table(hdr, tr, caption=f"{label}: two reference points of the front.")


def checkpoint_stats(path):
    recs = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
    proven = sum(1 for r in recs if r.get("status") == "OPTIMAL")
    return len(recs), proven


def level_slice(recs, horizon, limit):
    """Points of the f2 <= LEVEL sweep of certify_front.py, and whether they form an exact slice.
    Exact if every solve proved its profit stage and the chain eps = horizon, f3 - 1, ... reaches the
    empty schedule with strictly decreasing profits: the best profit is then known for every duration
    limit (it is constant between a solution's f3 and its limit) and no point is dominated.
    A stage that hit the time limit used >= `limit` seconds, so a TIME_LIMIT(<later stage>) label with a
    total runtime < 2 * limit means only that later stage timed out (valid for old and new labels)."""
    by_eps = {r["f3_limit"]: r for r in recs}
    eps, prev, pts, exact = horizon, None, [], True
    while True:
        r = by_eps.get(eps)
        if r is None or not r["found"]:
            return pts, False
        st = r["status"] or ""
        profit_proven = st == "OPTIMAL" or (st.startswith("TIME_LIMIT(") and st != "TIME_LIMIT(profit)"
                                             and r["runtime"] < 2 * limit)
        p = r["objectives"]["profit"]
        exact &= profit_proven and (prev is None or p < prev)
        if not r["schedule"]:
            return pts, exact
        pts.append((p, r["objectives"]["max_projects"], r["objectives"]["max_duration"]))
        prev, eps = p, int(r["objectives"]["max_duration"]) - 1


# ------------------------------------------------------------------ main
def main():
    V = {}
    group_file = HERE / "group.txt"
    if group_file.exists():
        names = [l.strip() for l in group_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        V["GROUP"] = "Group: " + ", ".join(names)
    else:
        V["GROUP"] = '<span style="color:#c00">Group: [names to complete in docs/report/group.txt]</span>'
    V["DATE"] = "November 2026"

    # instances
    inst_rows, ratio = instance_rows()
    V["INSTANCE_TABLE"] = table(
        ["Instance", "H", "|S|", "|J|", "|Q|", "Demand", "Capacity", "Demand / capacity",
         "x created / S×J×Q×T", "Model vars / constraints"], inst_rows,
        caption="Table 1. Instances. Demand = person-days required by all projects; capacity = working "
                "days of all engineers (leave excluded), ignoring skills. Last two columns: size of the final model.")
    undo = [100 * (1 - r[6] / r[5]) for r in inst_rows]
    V["UNDOABLE_RANGE"] = f"{min(undo):.0f}–{max(undo):.0f}%"
    V["STEP_VS_BIGM_VARS"] = f"about {100 * ratio:.0f}%"
    loc = 0
    for p in list((ROOT / "src").rglob("*.py")) + [ROOT / f for f in
                                                   ["run_pareto.py", "benchmark.py", "certify_front.py"]]:
        loc += len(p.read_text(encoding="utf-8").splitlines())
    V["LOC"] = f"{round(loc, -2):,}"

    # toy
    toy, toy_s = front("toy"), load_json(RES / "toy" / "summary.json")
    toy_json = load_json(RES / "toy" / "pareto_front.json")
    V["TOY_N"], V["TOY_SOLVES"], V["TOY_REC"] = toy_s["n_points"], toy_s["n_mip_solves"], toy_s["recommended_id"]
    V["TOY_UNSUP"] = toy_s["n_points"] - toy_s["n_supported"]
    V["TOY_TABLE"] = table(
        ["#", "Profit", "Max projects / engineer", "Longest project", "Late", "Completed projects", "Type"],
        [[r["id"], fmt(float(r["profit"])), r["max_projects"], r["max_duration"], r["late_projects"],
          jobs_label(p["completed_jobs"]),
          ("supported" if r["supported"] == "True" else "<b>unsupported</b>") +
          (" · compromise" if int(r["id"]) == toy_s["recommended_id"] else "")]
         for r, p in zip(toy, toy_json)],
        caption="Table 2. Toy: the complete non-dominated set (f1, f2, f3); late projects (f4) reported.",
        left=(5, 6))

    # medium
    med, med_s = front("medium"), load_json(RES / "medium" / "summary.json")
    rts = [json.loads(l)["runtime"] for l in
           (RES / "medium" / "checkpoint.jsonl").read_text(encoding="utf-8").splitlines()]
    V.update(MED_N=med_s["n_points"], MED_SUP=med_s["n_supported"],
             MED_UNSUP=med_s["n_points"] - med_s["n_supported"], MED_SOLVES=med_s["n_mip_solves"],
             MED_MIN=f"{med_s['runtime_sec'] / 60:.0f}", MED_MEDIAN=f"{statistics.median(rts):.0f}",
             MED_SLOWEST=f"{max(rts):.0f}")
    V["MED_KEYROWS"] = key_rows(med, med_s["recommended_id"], "Medium")
    best_med = max(float(r["profit"]) for r in med)
    V["MED_PRICE_DURATION"], V["MED_PRICE_FOCUS"] = price_tables(med, best_med, True, "Medium")

    # large
    lm, lm_s = front("large_merged"), load_json(RES / "large_merged" / "summary.json")
    best_large = max(float(r["profit"]) for r in lm)
    V.update(LARGE_N=lm_s["n_points"], LARGE_MAXP=fmt(best_large))
    a_n, a_p = checkpoint_stats(RES / "large" / "checkpoint.jsonl")
    b_n, b_p = checkpoint_stats(RES / "large_sequential" / "checkpoint.jsonl")
    sa, sb = load_json(RES / "large" / "summary.json"), load_json(RES / "large_sequential" / "summary.json")
    runs = [["A: one weighted MIP per point, 60 s per solve, step jump", sa["n_points"], a_n, a_p,
             f"{sa['runtime_sec'] / 3600:.1f} h (budget)"],
            ["B: profit, then f3, then f2 in sequence, 60 s per stage", sb["n_points"], b_n, b_p,
             f"{sb['runtime_sec'] / 3600:.1f} h (budget)"]]
    cert_dir = RES / "large_certify"
    cert_ok = all((cert_dir / f).exists() for f in ["summary.json", "certificate.csv", "certificate.jsonl"])
    if cert_ok:
        cs = load_json(cert_dir / "summary.json")
        cert = read_csv(cert_dir / "certificate.csv")
        recs = [json.loads(l) for l in (cert_dir / "certificate.jsonl").read_text(encoding="utf-8").splitlines()]
        lm_json = load_json(RES / "large_merged" / "pareto_front.json")
        kept_c = sum(1 for p in lm_json if p["status"] == "CERTIFY")
        c_proven = sum(1 for r in recs if r.get("status") == "OPTIMAL")
        runs.append(["C: certificate, 120 s per solve",
                     f"{cs['n_points']} new, {kept_c} kept", cs["n_mip_solves"], c_proven,
                     f"{cs['runtime_sec'] / 3600:.1f} h"])
    runs.append([f"<b>Merged</b> (non-dominated union, re-checked)", f"<b>{lm_s['n_points']}</b>",
                 lm_s["n_mip_solves"], "", f"{lm_s['runtime_sec'] / 3600:.1f} h"])
    V["LARGE_RUNS"] = table(["Run", "Points", "Solves", "Solves proven optimal", "Time"], runs,
                            caption="Table. Large instance runs. For C: schedules it found, and how many of "
                                    "them are on the merged front.")

    if cert_ok:
        num = lambda v: float(v) if v not in ("", "None", None) else None  # noqa: E731
        gaps = [num(r["gap_pct"]) for r in cert if num(r["gap_pct"]) is not None]
        gaps_b = [num(r["gap_before_pct"]) for r in cert if num(r.get("gap_before_pct")) is not None]
        proven = sum(1 for g in gaps if g == 0)
        gains = [float(r["best_found"]) - float(r["profit_front"]) for r in cert]
        improved = sum(1 for g in gains if g > 0.5)
        n = len(cert)
        n_pir = sum(1 for r in cert if r["status"] == "PROVEN_IN_RUN")
        n_gub = sum(1 for r in cert if r["status"] == "PROVEN_GLOBAL_UB")
        buckets = [("0% (proven optimal)", lambda g: g == 0), ("(0, 1%]", lambda g: 0 < g <= 1),
                   ("(1, 2%]", lambda g: 1 < g <= 2), ("(2, 5%]", lambda g: 2 < g <= 5),
                   ("(5, 10%]", lambda g: 5 < g <= 10), ("(10, 20%]", lambda g: 10 < g <= 20),
                   ("> 20%", lambda g: g > 20)]
        brows = [[b, sum(1 for g in gaps_b if f(g)), sum(1 for g in gaps if f(g))] for b, f in buckets]
        med_gap, med_b = statistics.median(gaps), statistics.median(gaps_b)
        gub = cs["global_ub"]
        # where the gaps are: high-profit boxes vs the rest
        top = [num(r["gap_pct"]) for r in cert if float(r["best_found"]) >= 0.9 * best_large]
        big = [r for r in cert if num(r["gap_pct"]) > 20]
        big_txt = (f" All {len(big)} gaps above 20% have moderately tight limits "
                   f"(\\(f_2\\le {max(int(r['f2']) for r in big)}\\), \\(f_3\\le {max(int(r['f3']) for r in big)}\\)), "
                   f"where neither the schedules nor the bounds converged within 120&nbsp;s." if big else "")
        # how much of the first approximation (runs A + B) survived
        old_dir = RES / "large_merged_before_certify"
        key = lambda r: (float(r["profit"]), f_int(r, "max_projects"), f_int(r, "max_duration"))  # noqa: E731
        old_keys = {key(r) for r in front(old_dir.name)}
        dominated_old = len(old_keys - {key(r) for r in lm})
        # the f2 <= 1 slice
        lvl_pts, lvl_exact = level_slice([r for r in recs if r["key"].startswith("level:")],
                                         ProblemInstance.from_json(ROOT / "data" / "large_instance.json").horizon,
                                         cs["time_per_solve"])
        n_lvl = sum(1 for r in recs if r["key"].startswith("level:"))
        lvl_txt = (f"<p>Run C also swept the level <b>\\(f_2\\le 1\\)</b> (one project per engineer), which runs A "
                   f"and B never reached: {n_lvl} lexicographic solves. Each one proves the best profit for its "
                   f"duration limit, and the next limit starts one day below the duration just found, so no limit "
                   f"is skipped: this slice of the front is <b>exact</b> ({len(lvl_pts)} points, best profit "
                   f"{max(p for p, _, _ in lvl_pts):.0f}).</p>" if lvl_exact and lvl_pts else "")
        V["LARGE_CERT"] = (
            f"<p><b>Quality certificate (run C).</b> For each of the {n} non-empty points "
            f"\\((p_i, a_i, b_i)\\) of the front of runs A and B we solved \\(\\max f_1\\) s.t. "
            f"\\(f_2\\le a_i,\\ f_3\\le b_i\\) for up to 120&nbsp;s, warm-started from the point "
            f"({n - n_pir - n_gub} solves; {n_pir} points were already proven optimal by their own run"
            + (f" and {n_gub} already reach the proven maximum profit" if n_gub else "") +
            f"). Gurobi returns the best schedule it finds, "
            f"\\(\\text{{best}}_i\\ge p_i\\), and a proven upper bound \\(UB_i\\), capped at {gub:.0f} (the proven "
            f"maximum profit, valid for every box). The best profit achievable within these limits therefore lies "
            f"in \\([\\text{{best}}_i, UB_i]\\), and \\(\\text{{gap}}_i=(UB_i-\\text{{best}}_i)/UB_i\\) is a "
            f"<i>proven upper bound</i> on the share of profit our answer may miss there.</p>"
            f"<p><b>What it showed.</b> The first approximation was weaker than it looked: the certificate found "
            f"<b>better schedules for {improved} of the {n} points</b> (up to +{max(gains):.0f} profit), and "
            f"<b>{dominated_old} of the {len(old_keys)} points of A + B are dominated</b> in the merged front. "
            f"After certification the median gap is <b>{med_gap:.1f}%</b> (it was {med_b:.1f}%), {proven} of the "
            f"{n} boxes are proven optimal, and the worst gap is {max(gaps):.1f}%. The gaps are smallest where a "
            f"decision is most likely to be made: for the {len(top)} boxes whose best profit is at least 90% of "
            f"the maximum, the median gap is {statistics.median(top):.1f}% (largest {max(top):.1f}%).{big_txt}</p>"
            + table(["Gap (UB − profit) / UB", "Boxes before C", "Boxes after C"], brows,
                    caption="Table. Certified gaps, one box per point of A + B: the original point (before C) and "
                            "the best schedule found by C (after C), both measured against C's bounds.")
            + lvl_txt)
        V["CERT_SENTENCE"] = (
            f"The maximum profit ({fmt(best_large)}) and the best profit for {proven} of the {n} (f2, f3) limit pairs "
            f"we checked are proven optimal" + (", as is the whole slice with one project per engineer"
                                                 if lvl_exact else "") +
            f"; for the other pairs, the solver's bounds prove that our profit is at most {max(gaps):.0f}% below the "
            f"best achievable (median over all pairs {med_gap:.1f}%). This certification also revealed that "
            f"{dominated_old} of the {len(old_keys)} points of our first approximation were dominated.")
        V["LIM_LARGE"] = (f"For {n - proven} of the {n} limit pairs our profit is not proven optimal: it may be up to "
                          f"{max(gaps):.1f}% below the best achievable within the same limits (median over all "
                          f"pairs {med_gap:.1f}%), and trade-offs that no run explored may be missing. It must be "
                          f"presented as “the best trade-offs found, with certified gaps”, not as the front.")
    else:
        V["LARGE_CERT"] = '<p class="warn">Certificate pending (results/large_certify not found).</p>'
        V["CERT_SENTENCE"] = f"Only the maximum profit ({fmt(best_large)}) is proven optimal."
        V["LIM_LARGE"] = "Only the maximum profit is proven; the other points carry no guarantee."
    V["LARGE_KEYROWS"] = key_rows(lm, lm_s["recommended_id"], "Large (merged)")
    V["LARGE_PRICE_DURATION"], V["LARGE_PRICE_FOCUS"] = price_tables(lm, best_large, False, "Large")

    # f4 on toy
    f4_rows = []
    for d, sec in [("toy_max_projects_late_projects", "f1, f2, f4"),
                   ("toy_max_duration_late_projects", "f1, f3, f4")]:
        if (RES / d / "pareto_front.csv").exists():
            for r in front(d):
                f4_rows.append([sec, r["id"], fmt(float(r["profit"])), r["max_projects"], r["max_duration"],
                                r["late_projects"], "yes" if r["supported"] == "True" else "<b>no</b>"])
    V["TOY_F4_TABLE"] = table(["Criteria searched", "#", "Profit", "Max proj./eng.", "Longest", "Late",
                               "Supported"], f4_rows,
                              caption="Table. Toy fronts with the late-projects criterion (both exact).")

    # benchmark
    bench = read_csv(RES / "benchmark.csv")
    brows = [[f"{r['H']}, {r['S']}, {r['J']} (seed {r['seed']})", f"{int(r['n_vars']):,}",
              f"{float(r['max_profit']):.0f}", f"{float(r['profit_time_s']):.2f} s",
              f"{r['front_points']} / {r['front_solves']} / {float(r['front_time_s']):.0f} s",
              "yes" if r["front_exact"] == "True" else "<b>no</b>"] for r in bench]
    V["BENCH_TABLE"] = table(["Size (H, |S|, |J|)", "Variables", "Max profit", "Proven in",
                              "Front: points / solves / time", "Front exact?"], brows,
                             caption="Table. Generated instances (max-profit solve proven optimal on all 12).")

    html = (HERE / "report_template.html").read_text(encoding="utf-8")
    for k, v in V.items():
        html = html.replace("{{" + k + "}}", str(v))
    assert "{{" not in html, html[html.index("{{"):html.index("{{") + 60]
    out_html = HERE / "report.html"
    out_html.write_text(html, encoding="utf-8")
    print("wrote", out_html)

    browser = next((p for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                shutil.which("msedge") or "", shutil.which("chrome") or ""]
                    if p and Path(p).exists()), None)
    if browser is None:
        print("no browser found: open report.html and print it to PDF")
        return
    pdf = HERE / "CompuOpti_report.pdf"
    profile = Path(tempfile.gettempdir()) / "dmp_report_browser_profile"
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
           f"--user-data-dir={profile}", f"--print-to-pdf={pdf}", out_html.as_uri()]
    subprocess.run(cmd, check=True, timeout=180, capture_output=True)
    print("wrote", pdf, f"({pdf.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
