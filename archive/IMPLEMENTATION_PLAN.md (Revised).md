# IMPLEMENTATION_PLAN.md (Revised)
# Summary (Updated)
Build a Python package and CLI that ingests an offices CSV and 8 scenario matrix CSVs, validates and normalizes them, solves the weighted constrained p-median for each k in 3..15, evaluates results, generates plots and reports, and emits a final recommendation.
**Explicit additions:**
* Enforce a canonical office ordering across all matrices and outputs
* Add a global feasibility precheck before optimization
* Emit a detailed validation report including corrections and distribution stats
* Record solver status and runtime per k
* Include assignment stability metrics across k

⠀Exclude route collection and external API integration from v1. Inputs are provided locally.

# Interfaces and File Set (Updated)
### Input contract
* offices.csv with:
  * office_id (unique, canonical key)
  * name
  * latitude
  * longitude
  * tier ∈ {1,2,3,4}
* Scenario matrices:
  * data/input/scenarios/<scenario>.csv
  * Square matrices with:
    * Header row = office_ids
    * First column = office_ids
  * Row labels must equal column labels

⠀Canonical ordering (NEW)
* Use office_id from offices.csv as the canonical index
* All matrices must be **reordered to match this index**
* Persist this ordering into all outputs

⠀
### Runtime config
### config/default.yaml includes:
* paths
* k_values (default: 3..15)
* sla_minutes (default: 120)
* tier_weights
* scenario_weights
* solver settings (time_limit_seconds, seed)
* output directory
* recommendation thresholds

⠀
### CLI surface (unchanged)
* warm-spare validate
* warm-spare preprocess
* warm-spare optimize
* warm-spare report
* warm-spare run

⠀
### Repo files (unchanged, but ordered)
### src/warm_spare/
###   config.py
###   models.py
###   io.py
###   preprocess.py
###   optimize.py
###   evaluate.py
###   recommend.py
###   plotting.py
###   reporting.py
###   cli.py

### Output contract (Expanded)
Per run under outputs/<run_id>/:
* metrics_by_k.csv (expanded fields)
* selected_sites_by_k.csv
* assignments_k_<k>.csv
* validation_report.md
* recommendation.md
* charts (PNG)

⠀
# Implementation Changes (Revised)
### 1\. Validation and ingestion (Expanded)
Enforce:
* Unique office_id
* Tier ∈ {1,2,3,4}
* Exactly 8 scenario files (configurable list)
* Each matrix:
  * Square
  * Row labels == column labels
  * Labels exactly match office_ids
  * No duplicates
  * Numeric, nonnegative values
  * No NaNs

⠀**Diagonal handling (Updated)**
* If diagonal ≠ 0:
  * Set to 0
  * Record count and file in validation report (warning)

⠀**Scenario weights (NEW)**
* If sum(weights) ≈ 1 → use as-is
* Else:
  * Normalize
  * Emit warning
* If any weight < 0 → fail validation

⠀
### 2\. Global feasibility precheck (NEW)
Before any optimization:
For each office i, check if ∃ j such that:
### D_max[i][j] <= sla_minutes
If any office has zero feasible candidates:
* Mark dataset infeasible
* Exit with clear error
* Include list of offending offices

⠀Also compute diagnostics:
* feasible_candidate_count per office
* min D_max per office

⠀Include in validation report.

### 3\. Preprocessing (Clarified)
* Symmetrize each scenario:

⠀D_sym[i][j] = (D[i][j] + D[j][i]) / 2
* Compute:

⠀D_avg = Σ (alpha_s * D_sym_s)
D_max = max_s (D_sym_s)
* Build feasibility mask:

⠀feasible[i][j] = (D_max[i][j] <= sla_minutes)

### 4\. Optimization engine (Refined)
Use OR-Tools (CP-SAT or Linear Solver; default CP-SAT).
Variables:
* x[j] ∈ {0,1}
* y[i][j] ∈ {0,1}

⠀Constraints:
* exact k
* one assignment per i
* y[i][j] ≤ x[j]
* disallow infeasible pairs via mask

⠀Loop:
### for k in k_values:
###   solve model
###   record:
###     - status
###     - objective
###     - runtime
If:
* infeasible → record and continue
* timeout without feasible solution → record status

⠀
### 5\. Evaluation (Expanded)
For each k:
Compute:
* weighted objective
* average drive time overall and by tier
* worst-case drive time overall and by tier
* max assigned D_max
* spare-site load counts
* SLA violation count (should be zero if feasible)
* assignment stability vs k-1 (NEW):
  * site overlap count
  * % offices reassigned

⠀
### 6\. Recommendation logic (Enhanced)
Base rule (unchanged):
* smallest feasible k where:
  * objective improvement < 5%
  * Tier 1 avg improvement < 5%
  * for two consecutive increments

⠀If none:
* select strongest knee in objective curve
* tie-break to smaller k

⠀**Additional guardrail (NEW)**
* Do not recommend k if Tier 2 avg degrades materially vs next k
  * threshold configurable (default: 2%)

⠀**Output must include:**
* chosen k
* top 2 alternatives
* rationale
* tradeoffs

⠀
### 7\. Visualization (unchanged)
Charts:
* objective vs k
* Tier 1 avg vs k
* Tier 2 avg vs k
* worst-case vs k

⠀Plus:
* simple lat/lon scatter for recommended k

⠀
# Test Plan (Expanded)
### Validation tests (unchanged + added)
Add:
* incorrect diagonal handling
* scenario weights normalization behavior

⠀
### Preprocessing tests (unchanged)

### Optimization tests (Expanded)
Add:
* global infeasibility detection
* SLA enforcement via mask

⠀
### Evaluation tests (Expanded)
Add:
* assignment stability metrics correctness
* metric monotonicity flags

⠀
### CLI smoke test (unchanged)

# Assumptions and Defaults (Clarified)
* No API integration in v1
* Default scenario weights included and normalized
* Tier weights per PRD
* SLA enforcement uses D_max
* Python 3.11 stack unchanged

⠀
# Validation Report Requirements (NEW SECTION)
### validation_report.md must include:
* office count
* scenario file inventory
* canonical ordering used
* corrections applied (diagonal fixes, normalization)
* per-scenario stats:
  * min, median, p95, max
* symmetry deviation before correction
* feasibility diagnostics:
  * min D_max per office
  * feasible candidate count per office

⠀
# Metrics Output Schema (NEW SECTION)
### metrics_by_k.csv must include:
* k
* solver_status
* solve_time_seconds
* objective
* objective_improvement_pct
* tier1_avg
* tier2_avg
* tier3_avg
* tier4_avg
* tier1_worst
* tier2_worst
* overall_worst
* max_assigned_dmax
* tier1_improvement_pct
* tier2_improvement_pct
* sla_violations
* avg_load_per_spare
* max_load_per_spare

⠀
# Implementation Order (NEW SECTION)
1 config + models
2 io + validation
3 preprocessing
4 synthetic test fixtures
5 optimization
6 evaluation
7 recommendation
8 reporting
9 plotting
10 CLI
11 end-to-end test

⠀
# Final Assessment
With these revisions:
* Data integrity risks are controlled
* Feasibility issues are caught early
* Outputs are decision-ready
* Recommendation logic is defensible
* The system is extensible without rework

⠀This is now solidly **production-grade analytics tooling**, not just a prototype.
