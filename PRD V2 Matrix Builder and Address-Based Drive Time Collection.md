# PRD V2: Address-Based Round-Trip Matrix Builder and Tier-Aware Warm Spare Optimization

## Objective
Build a v2 workflow that can generate warm-spare optimization datasets directly from office addresses, collect directional drive times through Google Maps, derive round-trip candidate matrices, screen real-time anomalies against static baselines, and feed the resulting datasets into the optimization pipeline.

## Core Changes from V1
- Source office data from `office_id,address,tier` instead of assuming prebuilt square matrices.
- Replace symmetrized one-way logic with explicit directional collection and derived round-trip modeling.
- Restrict warm-spare candidates to Tier 1-3 offices by default, while keeping Tier 4 offices in the demand set.
- Add a resumable matrix-builder command with cache-backed retry behavior and partial-run recovery.
- Quarantine suspicious real-time scenarios by default when they diverge too far from static baseline behavior.

## Data Contracts
### Office Input
Required columns:
- `office_id`
- `address`
- `tier`

Optional metadata:
- `name`
- `latitude`
- `longitude`
- `market`

### Market Config
A market file is the source of truth for selecting offices and output location. The CLI may reference it by alias with `--market <short_name>` or directly with `--market-file <path>`.

### Scenario Outputs
For each scenario, save:
- `<scenario_id>__office_to_candidate.csv`
- `<scenario_id>__candidate_to_office.csv`
- `<scenario_id>__round_trip.csv`

Rows are all offices. Columns are only eligible spare candidates.

## Matrix Builder Requirements
- Collect both office-to-candidate and candidate-to-office travel times.
- Use Google Maps static requests when `departure_policy=none`.
- Use real-time requests when `departure_policy=now`.
- Persist successful pair results immediately into SQLite.
- Retry transient failures with backoff and jitter.
- Exit non-zero when unresolved required pairs remain after retries.
- Resume from cache on rerun.

## Optimization Requirements
- Operate on rectangular candidate matrices.
- Use round-trip minutes for both objective and SLA feasibility.
- Never allow Tier 4 sites to be selected as spares.
- Continue producing recommendation and reporting artifacts by `k`.

## Anomaly Screening
- Require a static baseline whenever real-time scenarios are included.
- Compare real-time round-trip matrices against the static baseline.
- Flag a pair when real-time differs from static by more than `max(30 minutes, 50% of static, 3 standard deviations of scenario delta minutes)`.
- Quarantine a real-time scenario when flagged pairs exceed either:
  - `5%` of all valid pairs, or
  - `2%` of Tier 1/Tier 2 origin pairs.
- Exclude quarantined scenarios from generated analysis configs by default.
- Write `quarantined_pairs.csv` and `quarantine_manifest.json` so analysts can inspect the affected office/candidate pairs directly.
- Support targeted re-sampling of quarantined pairs by rerunning `build-matrix` with `--resolve-quarantine-from <build_dir_or_manifest>`.
- Support explicit analyst override for accepted anomalies through config or `--accept-quarantined-scenario <scenario_id>`.
- Build reports must distinguish:
  - localized plausible live congestion
  - broad scenario anomalies
  - request/API failures

## Deliverables
- `warm-spare build-matrix` command
- generated analysis config per build
- cache-backed matrix build outputs and manifests
- rectangular-matrix analysis support across validate/preprocess/optimize/report/run
- updated documentation and tests for v2 behavior
