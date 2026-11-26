# Required frozen strategy data

The original frozen strategy data has been restored from the matching TSE-KIMIBOT project archive.

- `strategies.json` — 51 BEST strategy definitions.
- `edges.json` — 108 parent-edge metadata records.

The strategy evaluator loads both files from this directory. The 51 strategies' `parent_a` and `parent_b` references were checked against `edges.json`; all references resolve.
