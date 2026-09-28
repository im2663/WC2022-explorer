"""
Converts the World Cup pipeline's CSV output into JSON for the frontend.
Run this whenever build_pipeline.py produces new CSVs.
"""
import csv
import json

PIPELINE_DIR = "/home/claude/wc-project/pipeline"
OUT_DIR = "/home/claude/wc-project/app/src/data"

BOOL_FIELDS = {"percentile_eligible"}


def cast(key, value):
    if value == "" or value is None:
        return None
    if key in BOOL_FIELDS:
        return value == "True"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def convert(csv_path, json_path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    records = [{k: cast(k, v) for k, v in row.items()} for row in rows]
    with open(json_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"{csv_path} -> {json_path} ({len(records)} records)")


if __name__ == "__main__":
    convert(f"{PIPELINE_DIR}/player_tournament_stats.csv", f"{OUT_DIR}/players.json")
    convert(f"{PIPELINE_DIR}/team_tournament_stats.csv", f"{OUT_DIR}/teams.json")
