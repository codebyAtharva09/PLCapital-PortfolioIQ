"""Milestone 2: the CLI exposure command uses the shared engine and states its denominators."""

import json
import re
import subprocess
import sys

import pytest

from portfolioiq.cli import main
from portfolioiq.config import PROJECT_ROOT


def run(capsys, *args):
    code = main(list(args))
    return code, capsys.readouterr().out


def test_exposure_shows_both_methods_with_denominators(capsys):
    code, out = run(capsys, "exposure", "Balanced Portfolio")
    assert code == 0
    assert "Denominator: Whole portfolio (100% of portfolio weight)" in out
    assert "Denominator: Eligible stock weight (36.00% of portfolio)" in out
    assert re.search(r"Technology\s+22\.00\s+61\.11\s+AAPL 12\.00, MSFT 10\.00", out)
    assert re.search(r"Bonds \(excluded\)\s+64\.00\s+-", out)
    assert re.search(r"Total\s+100\.00\s+100\.00", out)
    assert "Verification: INDEPENDENTLY_RECONCILED" in out
    assert out.isascii()


def test_residual_line_for_portfolio_11(capsys):
    code, out = run(capsys, "exposure", "Total Stock Market Index Fund")
    assert code == 0
    assert re.search(r"Unattributed weight residual\s+2\.00\s+-\s+not cash", out)
    assert re.search(r"Total\s+100\.00\s+100\.00", out)
    assert "WARNING" in out


def test_bond_only_portfolio_is_explained(capsys):
    code, out = run(capsys, "exposure", "Fixed Income Plus")
    assert code == 0
    assert "holds no eligible stocks" in out
    assert "Denominator: Not applicable: no eligible stocks" in out
    assert re.search(r"Bonds \(excluded\)\s+100\.00", out)


def test_generic_phrase_asks_for_clarification(capsys):
    code, out = run(capsys, "exposure", "bond fund")
    assert code == 2
    assert "Fixed Income Plus: 100.00% bonds by current_weight" in out
    assert "Sector exposure," not in out


def test_sql_syntax_is_rejected_before_matching(capsys):
    code, out = run(capsys, "exposure", "Growth Equity Fund'; DROP TABLE holdings;--")
    assert code == 3
    assert "rule SR1" in out and "Sector exposure," not in out


def test_json_output_for_the_q10_phrase(capsys):
    code, out = run(capsys, "exposure", "international equity", "--json")
    data = json.loads(out)
    assert code == 0
    assert data["resolution"]["portfolio_name"] == "International Equity Fund"
    assert {s["sector"]: s["method_a"] for s in data["exposure"]["sectors"]} == pytest.approx(
        {"Energy": 0.38, "Technology": 0.36, "Automotive": 0.15, "Consumer Staples": 0.11})
    assert data["exposure"]["verification"] == "INDEPENDENTLY_RECONCILED"


def test_tech_as_a_sector_uses_the_named_portfolio(capsys):
    code, out = run(capsys, "exposure", "tech", "--question", "What is the tech exposure of the Balanced Portfolio?")
    assert code == 0
    assert "Sector exposure, Balanced Portfolio" in out
    assert "refers to the Technology sector" in out


def test_module_entry_point_runs():
    completed = subprocess.run([sys.executable, "-m", "portfolioiq", "exposure", "Tech Innovation Fund"],
                               cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0
    assert re.search(r"Technology\s+100\.00\s+100\.00", completed.stdout)
