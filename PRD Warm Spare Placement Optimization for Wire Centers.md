# PRD: Warm Spare Placement Optimization for Wire Centers
# 1\. Overview
### Objective
Determine the optimal placement of a fixed number of warm spare router locations across ~100 wire centers in the Atlanta market such that:
* Drive time from any wire center to its nearest spare is minimized
* Higher-tier wire centers receive preferential (lower latency) coverage
* All wire centers are guaranteed a reachable spare within a strict SLA
* Spare locations are selected from existing wire centers (discrete set)
* Selected spare sites remain fixed after deployment

⠀
# 2\. Problem Definition
This is a **constrained weighted p-median optimization problem** with:
* Discrete candidate facilities (wire centers)
* Weighted demand points (tiers 1–4)
* Multi-scenario travel time inputs
* Hard service-level constraints

⠀
# 3\. Key Requirements
### 3.1 Functional Requirements
1 Select k spare locations from the set of ~100 wire centers
2 Assign every wire center to exactly one spare location
3 Minimize weighted drive time between each wire center and its assigned spare
4 Evaluate solutions for multiple values of k (range: 3 to 15)
5 Output performance metrics and optimal site selections per k

⠀
### 3.2 Hard Constraints (Non-Negotiable)
**Global SLA Constraint**
Every wire center must have at least one assigned spare such that:
* **Drive time ≤ 120 minutes**
* Must hold across:
  * All times of day
  * Weekday and weekend conditions

⠀Formally:
For every office `i`:
`\max_s D[s][i][assigned(i)] \le 120`

![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image.png)<!-- {"width":416} -->

Where `s` spans all traffic scenarios.

### 3.3 Optimization Objective
Minimize total weighted drive time:
`\min \sum_i \sum_j w_i \cdot D_{avg}[i][j] \cdot y[i][j]`

![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%202.png)<!-- {"width":361} -->
Where:
* `w_i` = tier weight
* `D_avg[i][j]` = weighted average drive time across scenarios
* `y[i][j]` = assignment decision

⠀
### 3.4 Tier Weighting
Recommended initial weights:
| **Tier** | **Weight** |
|:-:|:-:|
| Tier 1 | 10 |
| Tier 2 | 6 |
| Tier 3 | 3 |
| Tier 4 | 1 |
Purpose:
* Strongly bias optimization toward Tier 1 and 2
* Maintain coverage integrity for Tier 3 and 4

⠀
# 4\. Data Requirements
### 4.1 Input Data
**Wire Center Dataset**
Each office must include:
* office_id
* name
* latitude
* longitude
* tier (1–4)

⠀
**Drive Time Data**
For each scenario:
### `D[s][i][j] = drive time (minutes) between office i and j`
### Scenarios (8 total):
* Weekday: midnight, 6am, noon, 6pm
* Weekend: midnight, 6am, noon, 6pm

⠀
### 4.2 Data Processing
**Symmetrization**
Drive times must be made symmetric:
[ `D_{sym}[i][j] = \frac{D[i][j] + D[j][i]}{2}` ]
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%203.png)

**Aggregated Matrix**
[ `D_{avg}[i][j] = \sum_s \alpha_s \cdot D[s][i][j]` ]
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%204.png)

Where:
* `(\alpha_s) = scenario weight`
* Weekday and weekend weights should be roughly balanced, with mild bias toward weekday peak periods

⠀
**Worst-Case Matrix**
`D_{max}[i][j] = \max_s D[s][i][j]`
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%205.png)

Used to enforce SLA constraint.

# 5\. Optimization Model
### Decision Variables
* `x[j] ∈ {0,1}` 1 if office j is selected as a spare location
* `y[i][j] ∈ {0,1}` 1 if office i is assigned to spare j

⠀
### Constraints
**1** **Select exactly k spare sites**

⠀[ `\sum_j x[j] = k` ]
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%206.png)

**2** **Each office assigned to one spare**

⠀[ \sum_j y[i][j] = 1 \quad \forall i ]
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%207.png)

**3** **Assignment only to selected sites**

⠀[ `y[i][j] \le x[j]` ]
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%208.png)

**4** **SLA constraint (critical)**

⠀For all `(i, j)` pairs:
If: `D_{max}[i][j] > 120`
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%209.png)

Then: y[i][j] = 0
![](PRD%20Warm%20Spare%20Placement%20Optimization%20for%20Wire%20Centers/image%2010.png)

(Pre-filter invalid assignments before optimization)

# 6\. Technology Stack
### Core Libraries
* Python 3.x
* numpy
* pandas
* OR-Tools
* matplotlib or plotly

⠀
### APIs
* Google Maps Platform (Distance Matrix or Routes API)

⠀
### Optional
* joblib (parallelization)
* parquet or pickle (data persistence)

⠀
# 7\. System Architecture

[Office Data] → [Drive Time Collection] → [Matrix Builder]
                                         ↓
                                [Scenario Aggregation]
                                         ↓
                                [Optimization Engine]
                                         ↓
                                 [Evaluation Layer]
                                         ↓
                                     [Outputs]
# 8\. Workflow
### Step 1: Data Collection
* Generate all pairwise routes (100 x 100)
* Collect for all 8 scenarios
* Cache results

⠀
### Step 2: Preprocessing
* Symmetrize matrices
* Compute:
  * `D_avg`
  * `D_max`

⠀
### Step 3: Optimization Loop
For `k = 3 → 15`:
* Solve p-median problem
* Store:
  * selected spare sites
  * assignments
  * objective value

⠀
### Step 4: Evaluation
For each solution:
* Average Tier 1 drive time
* Average Tier 2 drive time
* Worst-case Tier 1 drive time
* Worst-case Tier 2 drive time
* Worst-case overall drive time
* SLA violations (should be zero)
* Load distribution across spare sites

⠀
### Step 5: Visualization
Generate:
* k vs total cost
* k vs Tier 1 avg time
* k vs Tier 2 avg time
* k vs worst-case time

⠀
# 9\. Success Criteria
A valid solution must:
* Satisfy SLA for all offices
* Show monotonic improvement as k increases
* Reveal a clear “elbow point” in cost reduction
* Provide stable and explainable spare site locations

⠀
# 10\. Deliverables
### Primary Outputs
* Optimal spare site list for each k
* Assignment map of offices to spares
* Performance metrics table

⠀
### Visual Outputs
* Elbow curve charts
* Coverage maps (optional geographic plotting)

⠀
### Final Recommendation
* Recommended number of spare sites
* Justification based on diminishing returns
* Tradeoff explanation

⠀
# 11\. Risks and Considerations
### 11.1 API Constraints
* Google API rate limits and cost
* Mitigation: caching and batching

⠀
### 11.2 Data Accuracy
* Traffic variability
* Construction or anomalies not captured

⠀
### 11.3 Model Assumptions
* Symmetric travel times
* Static spare locations
* Average-based optimization vs real-time dispatch

⠀
# 12\. Extensions (Future Work)
* Dynamic rerouting based on real-time traffic
* Incorporate failure probability or traffic load per office
* Multi-spare redundancy per region
* Time-of-day dependent assignment policies

⠀
# 13\. Implementation Notes
* Start with smaller subset (10–20 offices) to validate model
* Validate SLA constraint early before full runs
* Ensure solver performance scales with k range

⠀
# 14\. Definition of Done
The project is complete when:
* All drive time matrices are collected and validated
* Optimization runs successfully for k = 3–15
* SLA constraint holds for all solutions
* Elbow point is identified
* Recommendation is documented and defensible

