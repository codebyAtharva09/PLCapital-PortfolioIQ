"""Source-data notes from manual review (docs/DATA_FINDINGS.md, section 7; rule D6).

The supplied data is never modified. These notes are attached as caveats wherever they apply.
"""

SECURITY_NOTES: dict[str, str] = {
    "HD": (
        "Home Depot (HD) is classified as Consumer Staples in the supplied data, although common "
        "industry classification places it in Consumer Discretionary. The supplied classification "
        "is used as is."
    ),
}

ALL_US_NOTE = (
    "Every security in the supplied data is marked US, so this portfolio's name or strategy does "
    "not imply geographic diversification."
)
