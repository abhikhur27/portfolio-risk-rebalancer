# Portfolio Risk Rebalancer (Python)

Python CLI tool that converts a current holdings snapshot into an inverse-volatility rebalancing plan.

## Why this exists

When a portfolio drifts, it is easy to over-concentrate in high-volatility names. This tool produces a transparent rebalance plan using:

- current position value
- annualized volatility per asset
- optional cash contribution

It returns target weights, target value per position, and share deltas.

Realistically this doesnt need to be an independent repo as it is part of my main workflow. But the main workflow is currently private.

## Input CSV format

Required columns:

- `symbol`
- `shares`
- `price`
- `annual_volatility`

Example:

```csv
symbol,shares,price,annual_volatility
AAPL,20,198.50,0.24
MSFT,12,435.80,0.21
NVDA,8,1065.40,0.46
TLT,18,92.10,0.17
```

## Run

```bash
python rebalance.py --input sample_positions.csv
```

Include fresh cash and export a machine-readable trade plan:

```bash
python rebalance.py --input sample_positions.csv --cash 1500 --output-plan trade_plan.csv
```

## Output

The CLI prints:

- total current value
- target risk-balanced weights
- per-symbol value and share deltas
- gross turnover estimate

If `--output-plan` is provided, it also writes a CSV with actionable trade rows.

## Notes

- This is a planning utility, not investment advice.
- Volatility estimates should come from your own data pipeline.
- Fractional share output is supported for brokerages that allow it.

## Portfolio Positioning

- Project type: Python command-line utility
- Verification path: python rebalance.py --help and run sample_positions.csv through the CLI.

