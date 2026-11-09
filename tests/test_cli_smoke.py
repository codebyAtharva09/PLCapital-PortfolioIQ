"""CLI smoke tests: the installed commands run as real subprocesses, offline, with no API key.

Each command is started exactly as a user would start it. GEMINI_API_KEY is blanked in the child process, so
no Gemini request can be sent; demo replays read only the recorded ledger, which must stay byte-identical.
"""

import os
import subprocess
import sys

import pytest

from portfolioiq.config import PROJECT_ROOT

LEDGER = PROJECT_ROOT / "reports" / "api_usage.jsonl"


def run(*args):
    env = {**os.environ, "GEMINI_API_KEY": "", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", *args], cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)


@pytest.fixture(autouse=True)
def ledger_unchanged():
    before = LEDGER.read_bytes() if LEDGER.exists() else None
    yield
    assert (LEDGER.read_bytes() if LEDGER.exists() else None) == before


def test_exposure_command():
    result = run("portfolioiq", "exposure", "Balanced Portfolio")
    assert result.returncode == 0, result.stderr
    assert "Technology" in result.stdout and "61.11" in result.stdout and "INDEPENDENTLY_RECONCILED" in result.stdout


def test_integrity_command():
    result = run("portfolioiq", "integrity")
    assert result.returncode == 0, result.stderr
    assert "22 checks" in result.stdout and "0 FAIL" in result.stdout


def test_ask_without_a_key_explains_and_exits_4_without_a_traceback():
    result = run("portfolioiq", "ask", "How many portfolios do we have?")
    assert result.returncode == 4
    assert "GEMINI_API_KEY" in result.stdout and "Traceback" not in result.stdout + result.stderr


def test_ask_replay_answers_a_recorded_question_offline():
    result = run("portfolioiq", "ask", "--replay", "Which high-risk portfolio has the largest Technology exposure?")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("[DEMO / FIXTURE MODE]") and "Tech Innovation Fund" in result.stdout
    assert "API requests 0" in result.stdout


def test_sql_replay_and_input_validation():
    result = run("portfolioiq", "sql", "--replay", "Which portfolios have never had their performance recorded?")
    assert result.returncode == 0, result.stderr
    assert "DEMO / FIXTURE MODE" in result.stdout
    rejected = run("portfolioiq", "sql", "x" * 2001)
    assert rejected.returncode == 3 and "limited to 2,000 characters" in rejected.stdout


def test_streamlit_app_launches_and_serves_its_fonts():
    import socket
    import time
    import urllib.request

    port = 8765
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            pytest.skip(f"port {port} is in use")
    env = {**os.environ, "GEMINI_API_KEY": "", "PYTHONIOENCODING": "utf-8"}
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app/streamlit_app.py", "--server.port",
                               str(port), "--server.headless", "true"], cwd=PROJECT_ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline, healthy = time.time() + 60, False
        while time.time() < deadline and not healthy:
            try:
                healthy = urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=2).status == 200
            except OSError:
                time.sleep(0.5)
        assert healthy, "the app did not start"
        font = urllib.request.urlopen(f"http://localhost:{port}/app/static/fonts/IBMPlexSans-Variable-latin.woff2",
                                      timeout=5)
        assert font.status == 200 and font.read(4) == b"wOF2"
    finally:
        server.terminate()
        server.wait(timeout=30)


def test_evaluation_usage_summary_reads_the_ledger_only():
    import json

    recorded = sum(1 for line in LEDGER.read_text(encoding="utf-8").splitlines()
                   if line.strip() and json.loads(line).get("api_request"))
    result = run("portfolioiq_eval", "usage")
    assert result.returncode == 0, result.stderr
    assert f"API requests sent:   {recorded}" in result.stdout
