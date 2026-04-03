# Warm Spare Optimization

CLI application for building drive-time datasets from office addresses, validating round-trip scenario matrices, solving the constrained warm-spare placement problem, and generating reports and recommendations.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Core workflows

### 1. Build matrices from addresses

Prepare:

- a v2 config such as `config/v2_base.yaml`
- a market config such as `config/markets/example.yaml`
- an offices CSV with at least `office_id,address,tier`
- `GOOGLE_MAPS_API_KEY` in the environment

Run:

```bash
export GOOGLE_MAPS_API_KEY=your_key_here
warm-spare build-matrix --config config/v2_base.yaml --market example
```

This writes a timestamped matrix-build directory under the market output root and generates:

- directional matrices: `__office_to_candidate.csv`, `__candidate_to_office.csv`
- round-trip matrices: `__round_trip.csv`
- `candidate_sites.csv`
- `office_manifest.csv`
- `office_coordinates.csv`
- `build_report.md`
- `build_manifest.json`
- `analysis_config.yaml`

If unresolved API pairs remain after retries, the build exits non-zero and writes `unresolved_pairs.csv`. Successful pairs remain cached so reruns resume instead of restarting.

If a realtime scenario is quarantined, the build also writes:

- `quarantined_pairs.csv`
- `quarantine_manifest.json`

Resolution reruns automatically bind back to the cache database recorded in the original build manifest, so only the quarantined office/candidate pairs are refreshed.

Use those artifacts in one of two ways:

- rerun only quarantined office/candidate pairs on a later day:

```bash
warm-spare build-matrix \
  --config config/v2_base.yaml \
  --market example \
  --resolve-quarantine-from outputs/matrix_builds/<timestamp>_<market>
```

- accept a quarantined scenario after review and keep it in the generated analysis config:

```bash
warm-spare build-matrix \
  --config config/v2_base.yaml \
  --market example \
  --accept-quarantined-scenario realtime_now
```

For a first-pass static-only collection that skips realtime scenarios entirely:

```bash
warm-spare build-matrix \
  --config config/v2_base.yaml \
  --market example \
  --static-only
```

### 2. Run analysis on generated matrices

Use the generated config from the matrix-build output directory:

```bash
warm-spare run --config outputs/matrix_builds/<timestamp>_<market>/analysis_config.yaml
```

By default, `run` writes a full narrative recommendation report intended for planning and leadership review. It emits both:

- `recommendation.md` for Markdown/plain-text workflows
- `recommendation.html` as a self-contained browser-friendly report with embedded images

If you only want the compact recommendation summary, add:

```bash
warm-spare run \
  --config outputs/matrix_builds/<timestamp>_<market>/analysis_config.yaml \
  --short-report
```

## Input contracts

### Office CSV for matrix building

Required columns:

- `office_id`
- `address`
- `tier`

Optional metadata:

- `name`
- `latitude`
- `longitude`
- `market`

### Round-trip analysis matrices

For each configured scenario, the analysis pipeline expects either:

- `<scenario_id>__round_trip.csv`

or, for legacy compatibility only:

- `<scenario_id>.csv`

Rows are all offices. Columns are eligible spare candidates only.

## CLI modes

### `build-matrix`

Builds directional and round-trip candidate matrices from office addresses and market config.

Required extras:

- `--market <short_name>` or `--market-file <path>`
- optional: `--static-only`
- optional: `--resolve-quarantine-from <build_dir_or_manifest>`
- optional: `--accept-quarantined-scenario <scenario_id>` repeated as needed

Example:

```bash
warm-spare build-matrix --config config/v2_base.yaml --market example
```

### `validate`

Validates rectangular round-trip matrices and writes:

- `resolved_config.yaml`
- `run_metadata.json`
- `validation_report.md`

### `preprocess`

Runs validation plus preprocessing and writes:

- `office_feasibility.csv`
- `d_avg.csv`
- `d_max.csv`
- `feasibility_mask.csv`

### `optimize`

Runs the full analysis pipeline on prepared matrices.

### `report`

Current behavior matches `optimize`.

### `run`

Current behavior matches `optimize` and is the default end-to-end analysis command.

Optional reporting flag for `optimize`, `report`, and `run`:

- `--short-report`: write the compact recommendation summary instead of the full narrative report

## V2 configuration files

- `config/v2_base.yaml`: sample v2 config with static baseline plus realtime-now scenario definitions
- `config/markets/example.yaml`: sample market file resolved by `--market example`
- `config/default.yaml`: legacy-style analysis config for prebuilt scenarios
- `config/single_scenario_test.yaml`: legacy-style single-scenario test config

## Modeling changes in V2

- Optimization uses round-trip minutes, not symmetrized one-way times.
- Tier 4 offices remain demand points but are excluded from candidate spare sites by default.
- Static baseline scenarios are used to screen realtime anomalies.
- Realtime scenarios can be quarantined automatically and excluded from the generated analysis config.
- Pair anomaly screening uses the largest of three thresholds: `30` minutes, `50%` of static baseline, or `3σ` of the realtime-minus-static delta distribution for that scenario.
- Quarantined scenarios can be re-sampled later with `--resolve-quarantine-from` or explicitly accepted with `--accept-quarantined-scenario`.
- `build-matrix` now emits live progress to the terminal while collecting each scenario/direction.
- `build-matrix` now geocodes office addresses once, caches coordinates locally, and writes `office_coordinates.csv` for later report mapping.
- `run` now writes both `recommendation.md` and a self-contained `recommendation.html`; if `GOOGLE_MAPS_API_KEY` is set and Maps Static API is enabled, the report map uses a Google roadway basemap.
- `recommendation.html` includes the market overview map plus one zoomed detail map per selected warm spare, with the spare address and assigned-office tier counts beside each detail map.

## Validation and reporting outputs

Depending on mode, run directories may include:

- `resolved_config.yaml`
- `run_metadata.json`
- `validation_report.md`
- `office_feasibility.csv`
- `d_avg.csv`
- `d_max.csv`
- `feasibility_mask.csv`
- `one_way_dmax.csv`
- `metrics_by_k.csv`
- `selected_sites_by_k.csv`
- `recommended_selected_sites.csv`
- `assignments_k_<k>.csv`
- `recommended_sites_map.png`
- `spare_detail_map_<office_id>.png`
- `recommendation.md`
- `recommendation_<market>.md`
- `recommendation.html`
- `recommendation_<market>.html`
- plot PNGs

## Potential V3 and V4 enhancements

### V3: capacity-aware and load-balanced placement

- Add an optional load-balancing optimization mode so the solver does not over-concentrate demand on a single central spare site.
- Evaluate load balance inside the optimization itself, not as a manual post-processing step, so any extra site is chosen in the correct location and all office-to-spare assignments are recalculated consistently.
- Support business rules such as:
  - maximum assigned offices per spare site
  - weighted demand caps by assigned tier mix
  - soft penalties for concentrated load when a hard cap is not appropriate
- Allow the analysis to compare the best balanced `k` solution against the best balanced `k+1` solution when load concentration justifies the additional site.
- Preserve protection for outlier offices so balancing load does not materially degrade drive times for the most difficult-to-cover locations.

### V4: web-based operations and analysis UI

- Add a web application for end-to-end management of markets, inputs, runs, and outputs.
- Provide market management tools for:
  - uploading office CSVs
  - browsing market offices and candidate sites
  - reviewing geocodes and matrix coverage
- Provide run management tools for:
  - launching matrix builds and analyses
  - viewing logs and run status
  - opening completed analyses in separate browser tabs for side-by-side comparison
  - browsing prior runs and outputs by market
- Provide matrix and anomaly review tools for:
  - inspecting generated matrices
  - flagging quarantined scenarios or problematic site pairs
  - reviewing accepted versus excluded anomalies
- Provide administration and user settings pages for:
  - scenario and solver configuration
  - recommendation thresholds
  - API and system settings
  - user preferences and access controls
