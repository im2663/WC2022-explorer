# World Cup 2022 Player & Team Explorer

A data science / full-stack project built on StatsBomb's open event data
for the 2022 World Cup: a Python pipeline that aggregates raw match events
into player and team statistical profiles (with shrinkage-based percentile
benchmarking to correct for small-sample bias), and a React frontend for
exploring, filtering, and comparing the results.

## Structure

- `pipeline/` — Python data pipeline. Reads StatsBomb open data event/lineup
  files for all 64 matches, aggregates into `player_tournament_stats.csv`
  and `team_tournament_stats.csv`. See `pipeline/build_pipeline.py`.
- `app/` — React + TypeScript + Tailwind frontend. Reads the pipeline's
  output (converted to JSON via `app/scripts/csv_to_json.py`) and provides
  a searchable, filterable player/team explorer.

## Running the pipeline

Requires a local clone of [statsbomb/open-data](https://github.com/statsbomb/open-data).

```
cd pipeline
python3 build_pipeline.py
```

## Running the app

```
cd app
npm install
python3 scripts/csv_to_json.py   # regenerate JSON from the latest CSVs
npm run dev
```

## Methodology notes

- Percentile rankings use shrinkage estimation (blending a player's own
  rate with their position group's average, weighted by sample size) to
  avoid overstating confidence in small samples - e.g. a player with 5
  minutes and one shot doesn't get treated the same as one with 600+
  minutes. Players under 90 minutes are excluded from rate/percentile
  rankings entirely; raw totals are still shown.
- Penalty shootouts are excluded from all statistics (StatsBomb assigns
  them an xG value that would otherwise contaminate goals/shots/xG totals).
