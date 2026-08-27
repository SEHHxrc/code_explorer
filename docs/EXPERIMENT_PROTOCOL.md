# Dependency-graph A/B experiment protocol

## Goal

Measure whether dependency-graph augmentation improves repository answers compared with the same
model operating without graph material. This is prototype evaluation infrastructure, not two product modes.

## Controlled variables

Both lanes receive the same user question, configured provider/model, maximum agent steps, project
snapshot, neutral manifest facts, and a repo map rebuilt without graph-centrality ranking. Execution
order and left/right presentation order are randomized independently.

The graph lane additionally receives a bounded dependency-graph context and the dependency-neighbor
tool. The temporary control lane receives neither graph data nor graph tools. Estimated token metrics
are character-based estimates and are labelled as estimates; they are not provider billing totals.

Users see only left/right answers and metrics until both lanes finish and a blind review is submitted.
The review records correctness, completeness, evidence quality, hallucination control, preference,
and optional notes. Only then does the API reveal the group mapping.

## TEMPORARY CONTROL GROUP / 临时对照组 deletion manifest

If the graph lane wins, locate every marker with:

    rg -n "TEMPORARY CONTROL GROUP|临时对照组" backend frontend test docs

Then:

1. Delete backend/app/experiments/baseline/.
2. Remove the baseline strategy branch and baseline run creation from
   backend/app/experiments/service.py.
3. Remove baseline_run_id and blind comparison persistence through an explicit database migration
   if historical experiment records are no longer needed.
4. Delete the comparison API/router, comparison UI and frontend/src/services/experimentApi.js if
   experimentation is finished rather than continuing with a different control.
5. Delete test/test_experiment_isolation.py or retain only assertions for the graph path.
6. Keep the normal /api/agent route and graph-augmented production implementation; it never imports
   the control-group package.

Export experiment results before deleting tables when historical analysis is required. Source files
removed from Git remain recoverable from repository history.
