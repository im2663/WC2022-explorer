/**
 * Central stats registry.
 *
 * Every component that displays or filters on a stat (tables, filter
 * builders, radar charts) should read from this file rather than
 * hardcoding field names - adding a new stat to the pipeline later means
 * adding one entry here, not touching every component that shows stats.
 *
 * Two stat shapes:
 *  - "count": a raw total (goals, tackles won, etc). Supports all three
 *    display modes - total, per-90 (computed client-side as
 *    value / minutes_played * 90), and percentile (read directly from
 *    the `${key}_percentile` field already computed by the pipeline).
 *  - "rate": already a rate/percentage (pass completion %, duel win %).
 *    Has a percentile too, but no separate per-90 mode - the displayed
 *    value never changes with the mode toggle, only whether it's ranked.
 */

export type StatMode = "total" | "per90" | "percentile";

export type StatCategory =
  | "scoring"
  | "passing"
  | "progression"
  | "defensive"
  | "discipline";

interface CountStat {
  type: "count";
  key: string; // raw total field in the data
  percentileKey: string; // `${key}_percentile` field
  label: string;
  category: StatCategory;
  decimals?: number; // decimals to show in per-90 mode (totals are always whole numbers)
}

interface RateStat {
  type: "rate";
  key: string; // the stat IS the rate (already 0-100), e.g. pass_completion_pct
  percentileKey: string;
  label: string;
  category: StatCategory;
  unit?: "%"; // for display formatting
}

export type Stat = CountStat | RateStat;

export const STATS: Stat[] = [
  // Scoring
  { type: "count", key: "goals", percentileKey: "goals_percentile", label: "Goals", category: "scoring" },
  { type: "count", key: "non_penalty_goals", percentileKey: "non_penalty_goals_percentile", label: "Non-Penalty Goals", category: "scoring" },
  { type: "count", key: "shots", percentileKey: "shots_percentile", label: "Shots", category: "scoring" },
  { type: "count", key: "total_xg", percentileKey: "total_xg_percentile", label: "xG", category: "scoring", decimals: 2 },
  { type: "count", key: "non_penalty_xg", percentileKey: "non_penalty_xg_percentile", label: "Non-Penalty xG", category: "scoring", decimals: 2 },

  // Passing
  { type: "rate", key: "pass_completion_pct", percentileKey: "pass_completion_pct_percentile", label: "Pass Completion", category: "passing", unit: "%" },
  { type: "count", key: "key_passes", percentileKey: "key_passes_percentile", label: "Key Passes", category: "passing" },
  { type: "count", key: "assists", percentileKey: "assists_percentile", label: "Assists", category: "passing" },

  // Progression (carrying, dribbling, box entries)
  { type: "count", key: "carries", percentileKey: "carries_percentile", label: "Carries", category: "progression" },
  { type: "rate", key: "dribble_success_pct", percentileKey: "dribble_success_pct_percentile", label: "Dribble Success", category: "progression", unit: "%" },
  { type: "count", key: "dribbles_completed", percentileKey: "dribbles_completed_percentile", label: "Dribbles Completed", category: "progression" },
  { type: "count", key: "final_third_entries", percentileKey: "final_third_entries_percentile", label: "Final Third Entries", category: "progression" },
  { type: "count", key: "deep_completions", percentileKey: "deep_completions_percentile", label: "Deep Completions", category: "progression" },
  { type: "count", key: "passes_into_box", percentileKey: "passes_into_box_percentile", label: "Passes Into Box", category: "progression" },
  { type: "count", key: "carries_into_box", percentileKey: "carries_into_box_percentile", label: "Carries Into Box", category: "progression" },
  { type: "count", key: "touches_in_box", percentileKey: "touches_in_box_percentile", label: "Touches In Box", category: "progression" },

  // Defensive
  { type: "count", key: "pressures", percentileKey: "pressures_percentile", label: "Pressures", category: "defensive" },
  { type: "count", key: "ball_recoveries", percentileKey: "ball_recoveries_percentile", label: "Ball Recoveries", category: "defensive" },
  { type: "count", key: "interceptions", percentileKey: "interceptions_percentile", label: "Interceptions", category: "defensive" },
  { type: "count", key: "blocks", percentileKey: "blocks_percentile", label: "Blocks", category: "defensive" },
  { type: "rate", key: "duel_win_pct", percentileKey: "duel_win_pct_percentile", label: "Duel Win Rate", category: "defensive", unit: "%" },
  { type: "count", key: "duels_won", percentileKey: "duels_won_percentile", label: "Duels Won", category: "defensive" },
  { type: "rate", key: "tackle_success_pct", percentileKey: "tackle_success_pct_percentile", label: "Tackle Success", category: "defensive", unit: "%" },
  { type: "count", key: "tackles_won", percentileKey: "tackles_won_percentile", label: "Tackles Won", category: "defensive" },

  // Discipline
  { type: "count", key: "fouls_won", percentileKey: "fouls_won_percentile", label: "Fouls Won", category: "discipline" },
];

export const CATEGORY_LABELS: Record<StatCategory, string> = {
  scoring: "Scoring",
  passing: "Passing",
  progression: "Progression",
  defensive: "Defensive",
  discipline: "Discipline",
};

export const POSITION_GROUPS = [
  "Goalkeeper",
  "Center Back",
  "Full Back/Wing Back",
  "Defensive Midfield",
  "Center Midfield",
  "Wide Attacker",
  "Forward",
] as const;

/** Get a stat's displayed value for a given mode. `minutesPlayed` is
 * required for "per90" mode on count stats (rate stats ignore mode
 * entirely and always return their raw value). */
export function getStatValue(
  stat: Stat,
  record: Record<string, number | string | boolean | null>,
  mode: StatMode,
  minutesPlayed: number
): number | null {
  if (stat.type === "rate") {
    return mode === "percentile"
      ? (record[stat.percentileKey] as number | null)
      : (record[stat.key] as number | null);
  }
  // count stat
  const total = record[stat.key] as number | null;
  if (mode === "percentile") return record[stat.percentileKey] as number | null;
  if (mode === "total") return total;
  // per90 - respect the same eligibility floor used for percentiles; a
  // per-90 rate computed from a tiny sample (e.g. 5 minutes) is exactly
  // the kind of misleading number the pipeline's floor exists to prevent,
  // and that protection must carry over here since this is computed live
  // rather than read from a pre-blanked pipeline field. Team records have
  // no percentile_eligible field at all (no floor at that level, by
  // design) - only block when the field is explicitly false, i.e. a
  // player who exists but didn't clear the floor.
  if (record.percentile_eligible === false) return null;
  if (total == null || minutesPlayed <= 0) return null;
  return (total / minutesPlayed) * 90;
}
