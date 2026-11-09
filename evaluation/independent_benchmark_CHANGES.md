# Changes to the sealed independent benchmark files

**10 November 2026.** The `description` field in `independent_benchmark_dev_validation.json` and `independent_benchmark_heldout.json` pointed to an old
folder for the design document. It now gives the document's real path, `reports/independent_benchmark_design.md`. No question, expected answer or
other field changed. `independent_benchmark.sha256` was updated to match.

Hashes recorded before this correction, which the held-out result of 9 November cites:

```
6ccf357f314c85a7e90ec79f84233c4eae9f0afe62d515fe6dff0a230563e504  evaluation/independent_benchmark_dev_validation.json
988f189cec1480759ab0b9f0f4820ef9e20864803d52d417f9c70aafcc005266  evaluation/independent_benchmark_heldout.json
```
