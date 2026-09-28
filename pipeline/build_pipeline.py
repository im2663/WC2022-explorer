"""
World Cup 2022 tournament-level aggregation pipeline.

Reads raw per-match StatsBomb open data (events + lineups) and produces
clean, tournament-aggregated tables:
  - player_tournament_stats.csv
  - team_tournament_stats.csv

Includes shrinkage-based percentile benchmarking for both count-type stats
(goals, tackles, etc - shrunk via a phantom-minutes Poisson-Gamma-style
blend toward the player's position group average) and rate-type stats
(pass completion %, duel win %, etc - shrunk via a phantom-attempts
Beta-Binomial-style blend).
"""
import json
import math
import csv
from collections import defaultdict

DATA_DIR = "/home/claude/open-data/data"
OUT_DIR = "/home/claude/wc-project/pipeline"

# Pitch is 120 (x, length) by 80 (y, width). Attacking goal is at x=120, y=40.
FINAL_THIRD_X = 80.0
GOAL_X, GOAL_Y = 120.0, 40.0
DEEP_COMPLETION_RADIUS = 20.0  # yards from goal center, per StatsBomb's own published definition

# Standard penalty box: 18 yards deep from the goal line, 44 yards wide, centered on goal
BOX_X_MIN = 102.0
BOX_Y_MIN, BOX_Y_MAX = 18.0, 62.0


def in_box(loc):
    return loc[0] >= BOX_X_MIN and BOX_Y_MIN <= loc[1] <= BOX_Y_MAX


def distance_to_goal(loc):
    return math.hypot(loc[0] - GOAL_X, loc[1] - GOAL_Y)


# Event types treated as "the player touched the ball" for touches-in-box
# purposes. StatsBomb has no explicit Touch event, so this is a documented
# approximation: any on-ball action tagged with a location.
TOUCH_EVENT_TYPES = {
    "Pass", "Carry", "Shot", "Dribble", "Ball Receipt*",
    "Miscontrol", "Dispossessed", "Clearance",
}

# Position-group mapping used for percentile benchmarking. Collapses
# left/right symmetry and merges roles shown (empirically, via stat-profile
# comparison) to have similar output - e.g. Wide Attacker merges Winger +
# Wide Midfield + Attacking Midfield.
POSITION_GROUPS = {
    "Goalkeeper": "Goalkeeper",
    "Left Center Back": "Center Back", "Right Center Back": "Center Back", "Center Back": "Center Back",
    "Left Back": "Full Back/Wing Back", "Right Back": "Full Back/Wing Back",
    "Left Wing Back": "Full Back/Wing Back", "Right Wing Back": "Full Back/Wing Back",
    "Left Defensive Midfield": "Defensive Midfield", "Right Defensive Midfield": "Defensive Midfield",
    "Center Defensive Midfield": "Defensive Midfield",
    "Left Center Midfield": "Center Midfield", "Right Center Midfield": "Center Midfield",
    "Left Wing": "Wide Attacker", "Right Wing": "Wide Attacker",
    "Left Midfield": "Wide Attacker", "Right Midfield": "Wide Attacker",
    "Center Attacking Midfield": "Wide Attacker",
    "Left Attacking Midfield": "Wide Attacker", "Right Attacking Midfield": "Wide Attacker",
    "Center Forward": "Forward", "Left Center Forward": "Forward", "Right Center Forward": "Forward",
}

# Reliability tiers for player percentile benchmarking
HARD_FLOOR_MINUTES = 90     # below this: raw totals shown, no rate/percentile at all
RELIABLE_MINUTES = 450      # only players at/above this feed the shrinkage prior
SHRINKAGE_K = 270           # "trust the group average as if it were 3 matches of evidence"

PERCENTILE_STATS = [
    "goals", "non_penalty_goals", "shots", "total_xg", "non_penalty_xg",
    "key_passes", "assists", "carries", "dribbles_completed",
    "tackles_won", "interceptions", "pressures", "ball_recoveries", "blocks",
    "duels_won", "final_third_entries", "deep_completions",
    "passes_into_box", "carries_into_box", "touches_in_box", "fouls_won",
]

# Rate-type stats (successes/trials), shrunk using ATTEMPTS as the exposure
# unit, not minutes. k picked proportional to each stat's typical volume
# (passing accumulates fast; duels/tackles/dribbles are rare per player).
RATE_STATS = {
    "pass_completion_pct": ("passes_completed", "passes_attempted", 30),
    "duel_win_pct": ("duels_won", "duels_total", 5),
    "tackle_success_pct": ("tackles_won", "tackles_attempted", 5),
    "dribble_success_pct": ("dribbles_completed", "dribbles_attempted", 3),
}

# Team-level reliability tiers - much lighter touch than players, since
# team sample sizes (3-7 matches) are far less spread out than player
# minutes (5-868). No hard floor: every team played 3+ full matches.
TEAM_RELIABLE_MATCHES = 4
TEAM_SHRINKAGE_K = 90

TEAM_PERCENTILE_STATS = [
    "goals_for", "non_penalty_goals_for", "shots", "total_xg", "non_penalty_xg",
    "passes_completed", "final_third_entries", "deep_completions",
    "passes_into_box", "carries_into_box", "touches_in_box",
    "pressures", "ball_recoveries", "interceptions",
    "duels_won", "tackles_won", "fouls_won",
]

TEAM_RATE_STATS = {
    "pass_completion_pct": ("passes_completed", "passes_attempted", 300),
    "duel_win_pct": ("duels_won", "duels_total", 50),
    "tackle_success_pct": ("tackles_won", "tackles_attempted", 30),
    "dribble_success_pct": ("dribbles_completed", "dribbles_attempted", 20),
}


def time_to_seconds(t):
    if t is None:
        return None
    mm, ss = t.split(":")
    return int(mm) * 60 + int(float(ss))


def load_match_ids():
    return json.load(open(f"{DATA_DIR}/matches/43/106.json"))


def compute_period_end_times(events):
    """Max (minute:second) seen per period - StatsBomb's clock is
    CUMULATIVE across periods (period 2 continues from ~45:00, not reset
    to 0), so this is used as a fallback 'to' time for a player's last
    position stint (they played to the actual final whistle)."""
    period_end = defaultdict(int)
    for e in events:
        period = e.get("period")
        secs = e.get("minute", 0) * 60 + e.get("second", 0)
        if period is not None and secs > period_end[period]:
            period_end[period] = secs
    return period_end


def compute_minutes_played(lineup_entry, period_end):
    """Sum durations across all position stints for a player in one match.
    Period 5 (penalty shootouts) is excluded from "playing time" on
    purpose - shootouts aren't normally counted as minutes played."""
    total_seconds = 0
    primary_position_seconds = defaultdict(int)
    playing_periods = [p for p in period_end if p <= 4]
    match_end_seconds = period_end.get(max(playing_periods), 0) if playing_periods else 0

    for pos in lineup_entry.get("positions", []):
        from_sec = time_to_seconds(pos["from"])
        if pos["to"] is not None:
            to_sec = time_to_seconds(pos["to"])
        else:
            to_sec = match_end_seconds
        duration = max(0, to_sec - from_sec)
        total_seconds += duration
        primary_position_seconds[pos["position"]] += duration

    primary_position = None
    if primary_position_seconds:
        primary_position = max(primary_position_seconds, key=primary_position_seconds.get)
    return round(total_seconds / 60, 1), primary_position


def new_player_record():
    return {
        "matches_played": 0,
        "minutes_played": 0.0,
        "position_minutes": defaultdict(float),
        "team": None,
        "goals": 0,
        "penalty_goals": 0,
        "shots": 0,
        "shots_on_target": 0,
        "total_xg": 0.0,
        "penalty_xg": 0.0,
        "passes_attempted": 0,
        "passes_completed": 0,
        "key_passes": 0,
        "assists": 0,
        "carries": 0,
        "dribbles_attempted": 0,
        "dribbles_completed": 0,
        "dispossessed": 0,
        "miscontrols": 0,
        "pressures": 0,
        "ball_recoveries": 0,
        "interceptions": 0,
        "blocks": 0,
        "duels_won": 0,
        "duels_total": 0,
        "tackles_attempted": 0,
        "tackles_won": 0,
        "final_third_entries": 0,
        "deep_completions": 0,
        "passes_into_box": 0,
        "carries_into_box": 0,
        "touches_in_box": 0,
        "fouls_won": 0,
        "fouls_committed": 0,
        "yellow_cards": 0,
        "red_cards": 0,
    }


def new_team_record():
    return {
        "matches_played": 0,
        "minutes_played": 0.0,
        "goals_for": 0,
        "goals_against": 0,
        "shots": 0,
        "shots_on_target": 0,
        "total_xg": 0.0,
        "penalty_goals": 0,
        "penalty_xg": 0.0,
        "passes_attempted": 0,
        "passes_completed": 0,
        "dribbles_attempted": 0,
        "dribbles_completed": 0,
        "formations_used": defaultdict(int),
        "play_pattern_counts": defaultdict(int),
        "pressures": 0,
        "ball_recoveries": 0,
        "interceptions": 0,
        "duels_won": 0,
        "duels_total": 0,
        "tackles_attempted": 0,
        "tackles_won": 0,
        "final_third_entries": 0,
        "deep_completions": 0,
        "passes_into_box": 0,
        "carries_into_box": 0,
        "touches_in_box": 0,
        "fouls_won": 0,
        "fouls_committed": 0,
        "yellow_cards": 0,
        "red_cards": 0,
    }


def compute_percentiles(players):
    """Shrinkage-based percentile benchmarking for count-type player stats."""
    for p in players.values():
        primary_position = None
        if p["position_minutes"]:
            primary_position = max(p["position_minutes"], key=p["position_minutes"].get)
        p["primary_position"] = primary_position
        p["position_group"] = POSITION_GROUPS.get(primary_position)
        p["percentiles"] = {}
        p["non_penalty_goals"] = p["goals"] - p["penalty_goals"]
        p["non_penalty_xg"] = p["total_xg"] - p["penalty_xg"]

    groups = defaultdict(list)
    for p in players.values():
        if p["position_group"] and p["minutes_played"] >= HARD_FLOOR_MINUTES:
            groups[p["position_group"]].append(p)

    for group_name, members in groups.items():
        reliable = [p for p in members if p["minutes_played"] >= RELIABLE_MINUTES]
        for stat in PERCENTILE_STATS:
            reliable_total = sum(p[stat] for p in reliable)
            reliable_minutes = sum(p["minutes_played"] for p in reliable)
            if reliable_minutes == 0:
                continue
            prior_rate = 90.0 * reliable_total / reliable_minutes

            shrunk_values = {}
            for p in members:
                m = p["minutes_played"]
                raw_rate = 90.0 * p[stat] / m
                shrunk_values[id(p)] = (m * raw_rate + SHRINKAGE_K * prior_rate) / (m + SHRINKAGE_K)

            sorted_vals = sorted(shrunk_values.values())
            n = len(sorted_vals)
            for p in members:
                v = shrunk_values[id(p)]
                below = sum(1 for x in sorted_vals if x < v)
                p["percentiles"][stat] = round(100 * below / n, 1)


def compute_rate_percentiles(players):
    """Beta-Binomial-style shrinkage for rate stats, keyed on attempts."""
    for p in players.values():
        for pct_name, (succ_field, trials_field, k) in RATE_STATS.items():
            trials = p[trials_field]
            p[pct_name] = round(100 * p[succ_field] / trials, 1) if trials else None

    groups = defaultdict(list)
    for p in players.values():
        if p["position_group"] and p["minutes_played"] >= HARD_FLOOR_MINUTES:
            groups[p["position_group"]].append(p)

    for group_name, members in groups.items():
        reliable = [p for p in members if p["minutes_played"] >= RELIABLE_MINUTES]
        for pct_name, (succ_field, trials_field, k) in RATE_STATS.items():
            reliable_successes = sum(p[succ_field] for p in reliable)
            reliable_trials = sum(p[trials_field] for p in reliable)
            if reliable_trials == 0:
                continue
            prior_rate = reliable_successes / reliable_trials

            eligible_members = [p for p in members if p[trials_field] > 0]
            shrunk_values = {}
            for p in eligible_members:
                trials = p[trials_field]
                raw_rate = p[succ_field] / trials
                shrunk_values[id(p)] = (trials * raw_rate + k * prior_rate) / (trials + k)

            sorted_vals = sorted(shrunk_values.values())
            n = len(sorted_vals)
            for p in eligible_members:
                v = shrunk_values[id(p)]
                below = sum(1 for x in sorted_vals if x < v)
                p["percentiles"][pct_name] = round(100 * below / n, 1)


def compute_team_percentiles(teams):
    """Same shrinkage idea as players but lighter (smaller k, no hard
    floor, no position sub-grouping - one flat pool of all 32 teams)."""
    for t in teams.values():
        t["non_penalty_goals_for"] = t["goals_for"] - t["penalty_goals"]
        t["non_penalty_xg"] = t["total_xg"] - t["penalty_xg"]
        t["percentiles"] = {}

    reliable = [t for t in teams.values() if t["matches_played"] >= TEAM_RELIABLE_MATCHES]

    for stat in TEAM_PERCENTILE_STATS:
        reliable_total = sum(t[stat] for t in reliable)
        reliable_minutes = sum(t["minutes_played"] for t in reliable)
        if reliable_minutes == 0:
            continue
        prior_rate = 90.0 * reliable_total / reliable_minutes

        shrunk_values = {}
        for t in teams.values():
            m = t["minutes_played"]
            raw_rate = 90.0 * t[stat] / m
            shrunk_values[id(t)] = (m * raw_rate + TEAM_SHRINKAGE_K * prior_rate) / (m + TEAM_SHRINKAGE_K)

        sorted_vals = sorted(shrunk_values.values())
        n = len(sorted_vals)
        for t in teams.values():
            v = shrunk_values[id(t)]
            below = sum(1 for x in sorted_vals if x < v)
            t["percentiles"][stat] = round(100 * below / n, 1)


def compute_team_rate_percentiles(teams):
    for t in teams.values():
        for pct_name, (succ_field, trials_field, k) in TEAM_RATE_STATS.items():
            trials = t[trials_field]
            t[pct_name] = round(100 * t[succ_field] / trials, 1) if trials else None

    reliable = [t for t in teams.values() if t["matches_played"] >= TEAM_RELIABLE_MATCHES]

    for pct_name, (succ_field, trials_field, k) in TEAM_RATE_STATS.items():
        reliable_successes = sum(t[succ_field] for t in reliable)
        reliable_trials = sum(t[trials_field] for t in reliable)
        if reliable_trials == 0:
            continue
        prior_rate = reliable_successes / reliable_trials

        eligible_teams = [t for t in teams.values() if t[trials_field] > 0]
        shrunk_values = {}
        for t in eligible_teams:
            trials = t[trials_field]
            raw_rate = t[succ_field] / trials
            shrunk_values[id(t)] = (trials * raw_rate + k * prior_rate) / (trials + k)

        sorted_vals = sorted(shrunk_values.values())
        n = len(sorted_vals)
        for t in eligible_teams:
            v = shrunk_values[id(t)]
            below = sum(1 for x in sorted_vals if x < v)
            t["percentiles"][pct_name] = round(100 * below / n, 1)


def main():
    matches = load_match_ids()
    players = defaultdict(new_player_record)
    teams = defaultdict(new_team_record)

    for m in matches:
        match_id = m["match_id"]
        home_team = m["home_team"]["home_team_name"]
        away_team = m["away_team"]["away_team_name"]
        home_score = m["home_score"]
        away_score = m["away_score"]

        events = json.load(open(f"{DATA_DIR}/events/{match_id}.json"))
        lineups = json.load(open(f"{DATA_DIR}/lineups/{match_id}.json"))

        period_end = compute_period_end_times(events)
        playing_periods = [p for p in period_end if p <= 4]
        match_minutes = (period_end.get(max(playing_periods), 0) / 60.0) if playing_periods else 90.0

        players_in_match = set()
        for team_lineup in lineups:
            team_name = team_lineup["team_name"]
            teams[team_name]["matches_played"] += 1
            teams[team_name]["minutes_played"] += match_minutes
            if team_name == home_team:
                teams[team_name]["goals_for"] += home_score
                teams[team_name]["goals_against"] += away_score
            else:
                teams[team_name]["goals_against"] += home_score
                teams[team_name]["goals_for"] += away_score

            for p in team_lineup["lineup"]:
                if not p.get("positions"):
                    continue
                pname = p["player_name"]
                minutes, primary_pos = compute_minutes_played(p, period_end)
                if minutes <= 0:
                    continue
                rec = players[pname]
                rec["team"] = team_name
                rec["matches_played"] += 1
                rec["minutes_played"] += minutes
                if primary_pos:
                    rec["position_minutes"][primary_pos] += minutes
                players_in_match.add(pname)

                for card in p.get("cards", []):
                    if card["card_type"] == "Yellow Card":
                        rec["yellow_cards"] += 1
                        teams[team_name]["yellow_cards"] += 1
                    elif card["card_type"] == "Red Card":
                        rec["red_cards"] += 1
                        teams[team_name]["red_cards"] += 1

        for e in events:
            if e.get("period") == 5:
                # Penalty shootout - not part of normal play; StatsBomb still
                # assigns these shots an xG value which would wrongly inflate
                # goals/shots/xG totals if left in. Skip entirely.
                continue

            team_name = e.get("team", {}).get("name")
            pname = e.get("player", {}).get("name")
            etype = e["type"]["name"]

            if team_name:
                teams[team_name]["play_pattern_counts"][e.get("play_pattern", {}).get("name", "Unknown")] += 1

            if etype in ("Tactical Shift", "Starting XI") and team_name and "tactics" in e:
                teams[team_name]["formations_used"][e["tactics"]["formation"]] += 1

            if pname is None or team_name is None:
                continue
            prec = players[pname]
            trec = teams[team_name]

            if etype in TOUCH_EVENT_TYPES and "location" in e and in_box(e["location"]):
                prec["touches_in_box"] += 1
                trec["touches_in_box"] += 1

            if etype == "Shot":
                shot = e["shot"]
                is_penalty = shot.get("type", {}).get("name") == "Penalty"
                prec["shots"] += 1
                trec["shots"] += 1
                xg = shot.get("statsbomb_xg", 0.0)
                prec["total_xg"] += xg
                trec["total_xg"] += xg
                outcome = shot["outcome"]["name"]
                if outcome == "Goal":
                    prec["goals"] += 1
                if outcome in ("Goal", "Saved"):
                    prec["shots_on_target"] += 1
                    trec["shots_on_target"] += 1
                if is_penalty:
                    prec["penalty_xg"] += xg
                    trec["penalty_xg"] += xg
                    if outcome == "Goal":
                        prec["penalty_goals"] += 1
                        trec["penalty_goals"] += 1

            elif etype == "Pass":
                p = e["pass"]
                prec["passes_attempted"] += 1
                trec["passes_attempted"] += 1
                is_complete = "outcome" not in p
                if is_complete:
                    prec["passes_completed"] += 1
                    trec["passes_completed"] += 1
                if p.get("shot_assist") or p.get("goal_assist"):
                    prec["key_passes"] += 1
                if p.get("goal_assist"):
                    prec["assists"] += 1

                if is_complete and "location" in e and "end_location" in p:
                    start, end = e["location"], p["end_location"]
                    if start[0] < FINAL_THIRD_X <= end[0]:
                        prec["final_third_entries"] += 1
                        trec["final_third_entries"] += 1
                    if (not p.get("cross")
                            and distance_to_goal(start) > DEEP_COMPLETION_RADIUS
                            and distance_to_goal(end) <= DEEP_COMPLETION_RADIUS):
                        prec["deep_completions"] += 1
                        trec["deep_completions"] += 1
                    if not in_box(start) and in_box(end):
                        prec["passes_into_box"] += 1
                        trec["passes_into_box"] += 1

            elif etype == "Carry":
                prec["carries"] += 1
                c = e.get("carry", {})
                if "location" in e and "end_location" in c:
                    start, end = e["location"], c["end_location"]
                    if start[0] < FINAL_THIRD_X <= end[0]:
                        prec["final_third_entries"] += 1
                        trec["final_third_entries"] += 1
                    if (distance_to_goal(start) > DEEP_COMPLETION_RADIUS
                            and distance_to_goal(end) <= DEEP_COMPLETION_RADIUS):
                        prec["deep_completions"] += 1
                        trec["deep_completions"] += 1
                    if not in_box(start) and in_box(end):
                        prec["carries_into_box"] += 1
                        trec["carries_into_box"] += 1

            elif etype == "Dribble":
                d = e["dribble"]
                prec["dribbles_attempted"] += 1
                trec["dribbles_attempted"] += 1
                if d["outcome"]["name"] == "Complete":
                    prec["dribbles_completed"] += 1
                    trec["dribbles_completed"] += 1

            elif etype == "Dispossessed":
                prec["dispossessed"] += 1

            elif etype == "Miscontrol":
                prec["miscontrols"] += 1

            elif etype == "Pressure":
                prec["pressures"] += 1
                trec["pressures"] += 1

            elif etype == "Ball Recovery":
                prec["ball_recoveries"] += 1
                trec["ball_recoveries"] += 1

            elif etype == "Interception":
                prec["interceptions"] += 1
                trec["interceptions"] += 1

            elif etype == "Block":
                prec["blocks"] += 1

            elif etype == "Duel":
                prec["duels_total"] += 1
                trec["duels_total"] += 1
                duel = e.get("duel", {})
                outcome = duel.get("outcome", {}).get("name", "")
                if outcome in ("Won", "Success", "Success In Play", "Success Out"):
                    prec["duels_won"] += 1
                    trec["duels_won"] += 1
                if duel.get("type", {}).get("name") == "Tackle":
                    prec["tackles_attempted"] += 1
                    trec["tackles_attempted"] += 1
                    if outcome in ("Won", "Success In Play", "Success Out"):
                        prec["tackles_won"] += 1
                        trec["tackles_won"] += 1

            elif etype == "Foul Won":
                prec["fouls_won"] += 1
                trec["fouls_won"] += 1

            elif etype == "Foul Committed":
                prec["fouls_committed"] += 1
                trec["fouls_committed"] += 1

    compute_percentiles(players)
    compute_rate_percentiles(players)
    compute_team_percentiles(teams)
    compute_team_rate_percentiles(teams)
    write_player_csv(players)
    write_team_csv(teams)
    print(f"Players aggregated: {len(players)}")
    print(f"Teams aggregated: {len(teams)}")


def write_player_csv(players):
    fields = [
        "player", "team", "matches_played", "minutes_played", "primary_position",
        "goals", "penalty_goals", "non_penalty_goals",
        "shots", "shots_on_target",
        "total_xg", "penalty_xg", "non_penalty_xg",
        "passes_attempted", "passes_completed", "pass_completion_pct",
        "key_passes", "assists",
        "carries", "dribbles_attempted", "dribbles_completed", "dribble_success_pct",
        "dispossessed", "miscontrols",
        "pressures", "ball_recoveries", "interceptions", "blocks",
        "duels_won", "duels_total", "duel_win_pct",
        "tackles_attempted", "tackles_won", "tackle_success_pct",
        "final_third_entries", "deep_completions",
        "passes_into_box", "carries_into_box", "touches_in_box",
        "fouls_won", "fouls_committed", "yellow_cards", "red_cards",
        "position_group", "percentile_eligible",
        "goals_p90", "non_penalty_goals_p90", "shots_p90",
        "xg_p90", "non_penalty_xg_p90",
        "key_passes_p90", "pressures_p90", "dribbles_completed_p90",
    ] + [f"{stat}_percentile" for stat in PERCENTILE_STATS] + [f"{stat}_percentile" for stat in RATE_STATS]

    with open(f"{OUT_DIR}/player_tournament_stats.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for pname, r in players.items():
            minutes = r["minutes_played"] or 0.0001
            p90 = 90.0 / minutes
            primary_position = r.get("primary_position")
            row = {
                "player": pname,
                "team": r["team"],
                "matches_played": r["matches_played"],
                "minutes_played": r["minutes_played"],
                "primary_position": primary_position,
                "goals": r["goals"],
                "penalty_goals": r["penalty_goals"],
                "non_penalty_goals": r["goals"] - r["penalty_goals"],
                "shots": r["shots"],
                "shots_on_target": r["shots_on_target"],
                "total_xg": round(r["total_xg"], 3),
                "penalty_xg": round(r["penalty_xg"], 3),
                "non_penalty_xg": round(r["total_xg"] - r["penalty_xg"], 3),
                "passes_attempted": r["passes_attempted"],
                "passes_completed": r["passes_completed"],
                "pass_completion_pct": round(100 * r["passes_completed"] / r["passes_attempted"], 1) if r["passes_attempted"] else None,
                "key_passes": r["key_passes"],
                "assists": r["assists"],
                "carries": r["carries"],
                "dribbles_attempted": r["dribbles_attempted"],
                "dribbles_completed": r["dribbles_completed"],
                "dribble_success_pct": r.get("dribble_success_pct"),
                "dispossessed": r["dispossessed"],
                "miscontrols": r["miscontrols"],
                "pressures": r["pressures"],
                "ball_recoveries": r["ball_recoveries"],
                "interceptions": r["interceptions"],
                "blocks": r["blocks"],
                "duels_won": r["duels_won"],
                "duels_total": r["duels_total"],
                "duel_win_pct": r.get("duel_win_pct"),
                "tackles_attempted": r["tackles_attempted"],
                "tackles_won": r["tackles_won"],
                "tackle_success_pct": r.get("tackle_success_pct"),
                "final_third_entries": r["final_third_entries"],
                "deep_completions": r["deep_completions"],
                "passes_into_box": r["passes_into_box"],
                "carries_into_box": r["carries_into_box"],
                "touches_in_box": r["touches_in_box"],
                "fouls_won": r["fouls_won"],
                "fouls_committed": r["fouls_committed"],
                "yellow_cards": r["yellow_cards"],
                "red_cards": r["red_cards"],
                "goals_p90": round(r["goals"] * p90, 2),
                "non_penalty_goals_p90": round((r["goals"] - r["penalty_goals"]) * p90, 2),
                "shots_p90": round(r["shots"] * p90, 2),
                "xg_p90": round(r["total_xg"] * p90, 3),
                "non_penalty_xg_p90": round((r["total_xg"] - r["penalty_xg"]) * p90, 3),
                "key_passes_p90": round(r["key_passes"] * p90, 2),
                "pressures_p90": round(r["pressures"] * p90, 2),
                "dribbles_completed_p90": round(r["dribbles_completed"] * p90, 2),
                "position_group": r.get("position_group"),
                "percentile_eligible": r["minutes_played"] >= HARD_FLOOR_MINUTES,
            }
            for stat in PERCENTILE_STATS:
                row[f"{stat}_percentile"] = r.get("percentiles", {}).get(stat)
            for stat in RATE_STATS:
                row[f"{stat}_percentile"] = r.get("percentiles", {}).get(stat)

            if not row["percentile_eligible"]:
                for p90_field in ["goals_p90", "non_penalty_goals_p90", "shots_p90",
                                   "xg_p90", "non_penalty_xg_p90", "key_passes_p90",
                                   "pressures_p90", "dribbles_completed_p90"]:
                    row[p90_field] = None

            w.writerow(row)


def write_team_csv(teams):
    fields = [
        "team", "matches_played", "minutes_played", "goals_for", "goals_against", "goal_diff",
        "shots", "shots_on_target", "total_xg", "penalty_goals", "penalty_xg",
        "non_penalty_goals_for", "non_penalty_xg",
        "passes_attempted", "passes_completed", "pass_completion_pct",
        "dribbles_attempted", "dribbles_completed", "dribble_success_pct",
        "final_third_entries", "deep_completions",
        "passes_into_box", "carries_into_box", "touches_in_box",
        "pressures", "ball_recoveries", "interceptions",
        "duels_won", "duels_total", "duel_win_pct",
        "tackles_attempted", "tackles_won", "tackle_success_pct",
        "fouls_won", "fouls_committed", "yellow_cards", "red_cards",
        "most_used_formation", "top_play_pattern",
    ] + [f"{stat}_percentile" for stat in TEAM_PERCENTILE_STATS] + [f"{stat}_percentile" for stat in TEAM_RATE_STATS]

    with open(f"{OUT_DIR}/team_tournament_stats.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for tname, r in teams.items():
            most_used_formation = None
            if r["formations_used"]:
                most_used_formation = max(r["formations_used"], key=r["formations_used"].get)
            top_play_pattern = None
            if r["play_pattern_counts"]:
                top_play_pattern = max(r["play_pattern_counts"], key=r["play_pattern_counts"].get)
            row = {
                "team": tname,
                "matches_played": r["matches_played"],
                "minutes_played": round(r["minutes_played"], 1),
                "goals_for": r["goals_for"],
                "goals_against": r["goals_against"],
                "goal_diff": r["goals_for"] - r["goals_against"],
                "shots": r["shots"],
                "shots_on_target": r["shots_on_target"],
                "total_xg": round(r["total_xg"], 2),
                "penalty_goals": r["penalty_goals"],
                "penalty_xg": round(r["penalty_xg"], 2),
                "non_penalty_goals_for": r["goals_for"] - r["penalty_goals"],
                "non_penalty_xg": round(r["total_xg"] - r["penalty_xg"], 2),
                "passes_attempted": r["passes_attempted"],
                "passes_completed": r["passes_completed"],
                "pass_completion_pct": round(100 * r["passes_completed"] / r["passes_attempted"], 1) if r["passes_attempted"] else None,
                "dribbles_attempted": r["dribbles_attempted"],
                "dribbles_completed": r["dribbles_completed"],
                "dribble_success_pct": r.get("dribble_success_pct"),
                "final_third_entries": r["final_third_entries"],
                "deep_completions": r["deep_completions"],
                "passes_into_box": r["passes_into_box"],
                "carries_into_box": r["carries_into_box"],
                "touches_in_box": r["touches_in_box"],
                "pressures": r["pressures"],
                "ball_recoveries": r["ball_recoveries"],
                "interceptions": r["interceptions"],
                "duels_won": r["duels_won"],
                "duels_total": r["duels_total"],
                "duel_win_pct": r.get("duel_win_pct"),
                "tackles_attempted": r["tackles_attempted"],
                "tackles_won": r["tackles_won"],
                "tackle_success_pct": r.get("tackle_success_pct"),
                "fouls_won": r["fouls_won"],
                "fouls_committed": r["fouls_committed"],
                "yellow_cards": r["yellow_cards"],
                "red_cards": r["red_cards"],
                "most_used_formation": most_used_formation,
                "top_play_pattern": top_play_pattern,
            }
            for stat in TEAM_PERCENTILE_STATS:
                row[f"{stat}_percentile"] = r.get("percentiles", {}).get(stat)
            for stat in TEAM_RATE_STATS:
                row[f"{stat}_percentile"] = r.get("percentiles", {}).get(stat)
            w.writerow(row)


if __name__ == "__main__":
    main()
