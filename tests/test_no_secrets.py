"""Security: no file that git would commit contains a secret.

Checks every tracked or untracked-but-not-ignored file for credential-shaped strings and
for the actual key values configured locally. Findings name the file, never the value.
"""

import re
import subprocess

from dotenv import dotenv_values

from portfolioiq.config import PLACEHOLDER_API_KEYS, PROJECT_ROOT, load_settings

CREDENTIAL_PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "Google access token": re.compile(r"AQ\.[0-9A-Za-z_\-]{20,}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def _committable_files():
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return [PROJECT_ROOT / name for name in listing.split("\0") if name]


def _local_secret_values():
    values = {load_settings().gemini_api_key}
    dotenv_file = PROJECT_ROOT / ".env"
    if dotenv_file.exists():
        values |= set(dotenv_values(dotenv_file).values())
    return {v for v in values if v and v.strip() not in PLACEHOLDER_API_KEYS}


def test_dotenv_is_ignored_and_template_is_not():
    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=PROJECT_ROOT).returncode == 0

    assert ignored(".env")
    assert ignored(".env.local")
    assert not ignored(".env.example")


def test_no_committable_file_contains_a_secret():
    secrets = _local_secret_values()
    findings = []
    for path in _committable_files():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        name = path.relative_to(PROJECT_ROOT).as_posix()
        findings += [f"{name}: {label}" for label, p in CREDENTIAL_PATTERNS.items() if p.search(text)]
        if any(secret in text for secret in secrets):
            findings.append(f"{name}: a locally configured key value")
    assert not findings, "secrets found (values not shown): " + "; ".join(findings)
