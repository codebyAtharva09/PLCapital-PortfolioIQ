"""Compare production results with the pandas reference in portfolioiq_eval.independent_reference, and write the inventory.

    python scripts/run_independent_verification.py

Offline: SQL answers come from the recorded live run of the production configuration, and everything else is computed now.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolioiq.config import load_settings  # noqa: E402
from portfolioiq.exposure import calculate_exposure  # noqa: E402
from portfolioiq.sql_engine import ensure_reference_db, execute_sql, open_read_only  # noqa: E402
from portfolioiq_eval import independent_reference as ref  # noqa: E402

RUNS = ROOT / "reports" / "sprint_v2_3" / "candidate" / "runs"
OUT = ROOT / "reports" / "final_verification" / "independent_verification.json"
RECONCILED, VALIDATED, UNVERIFIABLE, FAILED = "INDEPENDENTLY_RECONCILED", "RESULT_VALIDATED", "NOT_INDEPENDENTLY_VERIFIABLE", "VERIFICATION_FAILED"
STATUS_BY_KIND = {"calculated": RECONCILED, "lookup": VALIDATED, "stored": UNVERIFIABLE}


def recorded() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in sorted(RUNS.glob("*/sql_outputs.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if (record.get("result") or {}).get("execution"):
                found[record["case"]["id"]] = record["result"]
    return found


def entry(metric, production, reference, agrees, difference, explanation, kind) -> dict:
    return {"metric": metric, "production_calculation": production, "independent_reference_calculation": reference, "difference": difference,
            "detail": explanation, "status": STATUS_BY_KIND[kind] if agrees else FAILED}


def question_entries(data) -> list[dict]:
    answers, references, out = recorded(), ref.official_references(data), []
    for case, expected in references.items():
        answer = answers.get(case)
        if answer is None:
            out.append({"metric": case, "production_calculation": "no recorded answer", "independent_reference_calculation": expected["note"], "difference": None, "detail": "not compared", "status": UNVERIFIABLE})
            continue
        agrees, difference, why = ref.compare(answer["execution"]["rows"], expected["rows"], ordered=expected["ordered"], tolerance=expected["tolerance"])
        out.append(entry(f"{case}: {answer['question']}", answer["sql"], expected["note"], agrees, difference, why, expected["kind"]))
    return out


def exposure_entries(db_path, data) -> list[dict]:
    out = []
    conn = open_read_only(db_path)
    try:
        for portfolio_id, name in zip(data["portfolios"]["portfolio_id"], data["portfolios"]["portfolio_name"]):
            produced = calculate_exposure(conn, int(portfolio_id))
            expected = ref.exposure(data, name)
            got_a = {s.sector: s.method_a for s in produced.sectors}
            got_b = {s.sector: s.method_b for s in produced.sectors}
            same_rows = set(got_a) == set(expected.method_a)
            differences = [abs(got_a[k] - expected.method_a[k]) for k in got_a if k in expected.method_a] + [abs(got_b[k] - expected.method_b[k]) for k in got_b if k in expected.method_b]
            differences += [abs(produced.residual - expected.residual), abs(produced.eligible_stock_weight - expected.stock_weight), abs(produced.total_weight - expected.total_weight)]
            worst = max(differences)
            agrees = same_rows and worst < 1e-9 and not expected.problems
            out.append(entry(f"Exposure, Methods A and B, residual: {name}", "calculate_exposure (Python and SQL paths)", "pandas groupby over holdings, securities and sectors CSVs", agrees, worst, "agrees" if agrees else "differs", "calculated"))
    finally:
        conn.close()
    return out


AGGREGATES = [
    ("Holding count per portfolio", "SELECT p.portfolio_name, COUNT(*) FROM holdings h JOIN portfolios p USING (portfolio_id) GROUP BY 1", lambda f: f.groupby("portfolio_name").size()),
    ("Distinct securities per portfolio", "SELECT p.portfolio_name, COUNT(DISTINCT h.security_id) FROM holdings h JOIN portfolios p USING (portfolio_id) GROUP BY 1", lambda f: f.groupby("portfolio_name")["security_id"].nunique()),
    ("Cost basis per portfolio", "SELECT p.portfolio_name, SUM(h.cost_basis) FROM holdings h JOIN portfolios p USING (portfolio_id) GROUP BY 1", lambda f: f.groupby("portfolio_name")["cost_basis"].sum()),
    ("Market value (quantity x current price) per portfolio", "SELECT p.portfolio_name, SUM(h.quantity * s.current_price) FROM holdings h JOIN securities s USING (security_id) JOIN portfolios p USING (portfolio_id) GROUP BY 1",
     lambda f: (f["quantity"] * f["current_price"]).groupby(f["portfolio_name"]).sum()),
    ("Sum of weights per portfolio", "SELECT p.portfolio_name, SUM(h.current_weight) FROM holdings h JOIN portfolios p USING (portfolio_id) GROUP BY 1", lambda f: f.groupby("portfolio_name")["current_weight"].sum()),
]


def aggregate_entries(db_path, data) -> list[dict]:
    out, positions = [], ref._positions(data)
    for metric, sql, build in AGGREGATES:
        result = execute_sql(db_path, sql)
        expected = [[name, value.item() if hasattr(value, "item") else value] for name, value in build(positions).items()]
        agrees, difference, why = ref.compare([list(r) for r in result.rows], expected)
        out.append(entry(metric, sql, "pandas over the CSVs", agrees, difference, why, "calculated"))
    stated = execute_sql(db_path, "SELECT portfolio_name, total_aum FROM portfolios")
    agrees, difference, why = ref.compare([list(r) for r in stated.rows], [[n, float(a)] for n, a in zip(data["portfolios"]["portfolio_name"], data["portfolios"]["total_aum"])])
    out.append(entry("Stated AUM per portfolio (source-stated; not derivable from holdings, which cover part of it)", "SELECT portfolio_name, total_aum", "portfolios.csv", agrees, difference, why, "stored"))
    for metric, sql, frame, date_column, column in [("Latest NAV per portfolio (stored; no units documented)", "SELECT p.portfolio_name, x.nav FROM portfolio_performance x JOIN portfolios p USING (portfolio_id) WHERE x.performance_date = (SELECT MAX(performance_date) FROM portfolio_performance)", "portfolio_performance", "performance_date", "nav"),
                                                    ("Latest beta per portfolio (stored; no return series to recompute it)", "SELECT p.portfolio_name, x.beta FROM risk_metrics x JOIN portfolios p USING (portfolio_id) WHERE x.calculation_date = (SELECT MAX(calculation_date) FROM risk_metrics)", "risk_metrics", "calculation_date", column := "beta")]:
        table = data[frame].merge(data["portfolios"], on="portfolio_id")
        table = table[pd_dates(table[date_column]) == ref.latest(data[frame], date_column)]
        result = execute_sql(db_path, sql)
        agrees, difference, why = ref.compare([list(r) for r in result.rows], [[n, float(v)] for n, v in zip(table["portfolio_name"], table[column])])
        out.append(entry(metric, sql, f"{frame}.csv at the latest date", agrees, difference, why, "stored"))
    return out


def pd_dates(series):
    import pandas as pd

    return pd.to_datetime(series)


def main() -> int:
    settings = load_settings()
    data = ref.load(settings.data_dir)
    db_path = ensure_reference_db(settings.data_dir, settings.schema_path, Path(tempfile.mkdtemp()))
    inventory = question_entries(data) + exposure_entries(db_path, data) + aggregate_entries(db_path, data)
    counts: dict[str, int] = {}
    for item in inventory:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "reference built with pandas from data/*.csv; recorded SQL answers from reports/sprint_v2_3/candidate/runs", "counts": counts, "inventory": inventory}, indent=1) + "\n", encoding="utf-8")
    print(counts)
    for item in inventory:
        if item["status"] == FAILED:
            print("FAILED:", item["metric"][:90], "|", item["detail"])
    return 1 if FAILED in counts else 0


if __name__ == "__main__":
    raise SystemExit(main())
