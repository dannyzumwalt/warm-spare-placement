# Warm Spare Optimization

CLI application for validating wire center travel-time inputs, solving the constrained weighted p-median problem for warm spare placement, and generating reports, charts, and recommendations.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[plotting]'
warm-spare run --config config/default.yaml
```

## Input layout

- `data/input/offices.csv`
- `data/input/scenarios/<scenario>.csv`

The scenario filenames are configured in the selected YAML config.

## Config files

- `config/default.yaml`: full 8-scenario production-style run
- `config/single_scenario_test.yaml`: single-matrix test run using `static_average.csv`

You choose the config at runtime:

```bash
warm-spare run --config config/default.yaml
warm-spare run --config config/single_scenario_test.yaml
```

## CLI modes

All commands write outputs to a timestamped run directory under `outputs/`.

### `validate`

Validates the inputs and writes:

- `resolved_config.yaml`
- `run_metadata.json`
- `validation_report.md`

Use this when you want to confirm file shape, labels, weights, canonical ordering, diagonal fixes, and feasibility diagnostics before generating preprocessing artifacts or solving.

Example:

```bash
warm-spare validate --config config/default.yaml
```

### `preprocess`

Runs validation plus preprocessing and writes:

- `resolved_config.yaml`
- `run_metadata.json`
- `validation_report.md`
- `office_feasibility.csv`
- `d_avg.csv`
- `d_max.csv`
- `feasibility_mask.csv`

Use this when you want to inspect the normalized matrices and feasibility mask before solving.

Example:

```bash
warm-spare preprocess --config config/default.yaml
```

### `optimize`

Runs the full analysis pipeline:

- validation
- preprocessing
- optimization for each configured `k`
- metrics generation
- recommendation generation
- plot generation when enabled in config

Outputs include everything from `preprocess` plus:

- `metrics_by_k.csv`
- `selected_sites_by_k.csv`
- `assignments_k_<k>.csv`
- `recommendation.md`
- PNG charts when plotting is enabled

Example:

```bash
warm-spare optimize --config config/default.yaml
```

### `report`

Current behavior is the same as `optimize`.

It exists as a workflow alias for a report-focused run and produces the same end-to-end outputs.

Example:

```bash
warm-spare report --config config/default.yaml
```

### `run`

Current behavior is the same as `optimize`.

Use this as the default end-to-end command when you simply want the full workflow without thinking about intermediate stages.

Example:

```bash
warm-spare run --config config/default.yaml
```

## Typical usage

### Full 8-scenario run

```bash
warm-spare validate --config config/default.yaml
warm-spare preprocess --config config/default.yaml
warm-spare run --config config/default.yaml
```

### Single-scenario test run

Provide these files:

- `data/input/offices.csv`
- `data/input/scenarios/static_average.csv`

Then run:

```bash
warm-spare run --config config/single_scenario_test.yaml
```

In that mode, the single matrix is used as both `D_avg` and `D_max`, which is useful for testing the rest of the pipeline before you have all 8 traffic scenarios.

## Output summary

Depending on mode, the run directory may include:

- `resolved_config.yaml`
- `run_metadata.json`
- `validation_report.md`
- `office_feasibility.csv`
- `d_avg.csv`
- `d_max.csv`
- `feasibility_mask.csv`
- `metrics_by_k.csv`
- `selected_sites_by_k.csv`
- `assignments_k_<k>.csv`
- `recommendation.md`
- plot PNGs
