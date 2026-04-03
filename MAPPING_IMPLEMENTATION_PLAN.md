# Mapping Implementation Plan

## Goal
Add a static report map that shows:
- all offices in the market
- office tier
- office assignment to the recommended warm spare
- recommended warm spare sites clearly highlighted
- generated locally as PNG
- embedded automatically into the final report

## Recommended Visual Design
Use:
- `color` = assigned warm spare group
- `marker shape` = office tier
- `larger outlined star overlay` = recommended warm spare site

This assigns one visual channel to one meaning:
- color answers: "which spare serves this office?"
- shape answers: "what tier is this office?"
- star answers: "which offices are the selected spare sites?"

This is preferred over assignment lines because:
- lines will get cluttered quickly with 100+ offices
- color grouping is easier to read at market scale
- the star overlay keeps selected spare sites obvious without hiding tier information

## Output Artifacts
New generated artifacts:
- `office_coordinates.csv`
- `recommended_sites_map.png`

Possible later additions:
- `recommended_sites_map_labeled.png`
- `recommended_sites_coordinates.csv`

The full report should embed `recommended_sites_map.png`.

## Data Requirements
The map requires coordinates for each office.

### Source
Use the existing Google API via address geocoding.

### Cache
Persist geocodes locally so offices are not re-geocoded every run.

Cache fields:
- `office_id`
- `input_address`
- `normalized_address`
- `latitude`
- `longitude`
- `geocode_status`
- `updated_at`

## Architecture Changes

### 1. Add geocoding subsystem
Create:
- `src/warm_spare/geocode.py`

Responsibilities:
- read office addresses
- fetch coordinates from Google when missing
- reuse cached coordinates when present
- return a coordinate table for plotting and reporting

### 2. Add coordinate cache
Use SQLite by default, consistent with the existing matrix-builder cache approach.

Suggested store:
- `geocode_cache.sqlite`
- table `office_geocodes`

Cache key:
- `office_id`
- address hash or raw address comparison to detect changes

Behavior:
- if office address is unchanged and coordinates are cached, reuse them
- if office address changed, refresh the geocode

### 3. Integrate geocoding into workflow
Best integration point:
- during `build-matrix`

Reason:
- that command already depends on addresses and Google APIs
- later `run` steps should stay deterministic and offline

Build output should include:
- `office_coordinates.csv`

The generated `analysis_config.yaml` should also carry a path to `office_coordinates.csv`, or the run/report path should use a clearly defined convention for resolving it from the matrix-build directory. This should be explicit, not inferred loosely at runtime.

### 4. Add mapping module
Create:
- `src/warm_spare/mapping.py`

Responsibilities:
- load office coordinates
- load assignments for the recommended `k`
- load the selected warm spare sites
- render the static map PNG

### 5. Extend report generator
Update reporting to:
- include `recommended_sites_map.png` when present
- add a short explanation of how to read the map

## Map Rendering Design

### Base layer
Plot all offices as points using latitude and longitude.

### Assignment encoding
Color each office by its assigned warm spare.

Approach:
- generate one categorical color per selected spare site
- each office inherits the color of its assigned spare

This makes service territories visible without drawing lines.

### Tier encoding
Use marker shapes by tier:
- Tier 1: `^`
- Tier 2: `s`
- Tier 3: `o`
- Tier 4: `x`

This preserves tier meaning independently of assignment color.

### Warm spare encoding
For selected spare offices:
- plot the normal tier marker in assignment color
- overlay a larger black-edged star
- label with `office_id`

This keeps both the tier identity and the spare identity visible.

### Labels
Default behavior:
- label warm spare sites only
- do not label every office

### Legend
Include:
- marker shape legend for tiers
- note that color indicates assigned warm spare
- star marker indicates selected warm spare sites

## Workflow Changes

### Build step
`build-matrix` should:
- geocode all offices if needed
- write `office_coordinates.csv`

### Run/report step
`run` should:
- if the resolved coordinate artifact exists and recommended assignments exist:
  - generate `recommended_sites_map.png`
  - embed it in the report

No extra CLI flag is required initially. Map generation should happen by default when coordinates are available.

## Failure Handling
If geocoding fails for some offices:
- cache successful results
- record unresolved geocodes
- do not fail the optimization run because of mapping alone
- skip map generation gracefully when required coordinates are missing

Recommended behavior:
- warning in the report
- map omitted if any recommended spare site lacks coordinates
- if only non-spare office coordinates are missing, allow the map to render with the resolved offices only and note the omission count in the report

## Testing Plan

### Unit tests
- geocode cache hit and miss behavior
- changed address forces refresh
- mapping groups offices by assigned-spare color
- warm spare star overlay is produced for selected sites
- report embeds the map path when the file exists

### Integration tests
- fake geocoder returns coordinates
- `build-matrix` writes `office_coordinates.csv`
- generated `analysis_config.yaml` resolves the coordinate artifact correctly
- `run` writes `recommended_sites_map.png`
- `recommendation.md` includes the image

## Report Changes
Add a new section to the full report:

### Recommended Coverage Map
Explanation:
- color shows which recommended warm spare serves each office
- marker shape shows office tier
- star markers indicate the selected warm spare locations

Then embed:
- `recommended_sites_map.png`

## Design Decisions
Recommended defaults:
- assignment encoded by color, not lines
- tier encoded by marker shape
- selected spare sites shown with star overlay
- geocode once during `build-matrix`, not during report generation
- cache aggressively and reroute only when addresses change

## Why This Approach Fits the Current System
It fits the existing workflow cleanly because:
- address lookups already live in `build-matrix`
- report generation already consumes static artifacts
- the result is reproducible and deck-friendly
- it avoids adding an interactive mapping stack or cluttered linework

## Implementation Sequence
1. add geocode cache and Google geocode client
2. geocode offices during `build-matrix`
3. persist `office_coordinates.csv`
4. add static mapping renderer
5. integrate map generation into `run`
6. embed the map in `recommendation.md`
7. tune marker and color choices on the Atlanta sample
