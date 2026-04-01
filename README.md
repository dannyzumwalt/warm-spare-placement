# Warm Spare Optimization

CLI application for validating wire center travel-time inputs, solving the constrained weighted p-median problem for warm spare placement, and generating reports, charts, and recommendations.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
warm-spare run --config config/default.yaml
```

## Input layout

- `data/input/offices.csv`
- `data/input/scenarios/<scenario>.csv`

The scenario filenames are configured in `config/default.yaml`.
