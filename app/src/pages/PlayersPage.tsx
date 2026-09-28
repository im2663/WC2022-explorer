import { useMemo, useState } from "react";
import playersData from "../data/players.json";
import {
  STATS,
  CATEGORY_LABELS,
  POSITION_GROUPS,
  getStatValue,
  type StatMode,
  type Stat,
} from "../statsRegistry";

type PlayerRecord = Record<string, number | string | boolean | null>;
const players = playersData as PlayerRecord[];

type Operator = ">=" | "<=";
interface StatFilter {
  id: number;
  statKey: string;
  operator: Operator;
  value: number;
}

// A reasonable default column set - one representative stat per category -
// so the table isn't overwhelming on first load. Fully customizable below.
const DEFAULT_VISIBLE_KEYS = [
  "total_xg",
  "key_passes",
  "carries",
  "tackles_won",
  "duel_win_pct",
];

function formatValue(stat: Stat, value: number | null, mode: StatMode): string {
  if (value == null) return "—";
  if (mode === "percentile") return `${Math.round(value)}`;
  const decimals =
    stat.type === "count" ? (mode === "per90" ? stat.decimals ?? 2 : 0) : 1;
  const suffix = stat.type === "rate" ? "%" : "";
  return `${value.toFixed(decimals)}${suffix}`;
}

export default function PlayersPage() {
  const [mode, setMode] = useState<StatMode>("total");
  const [search, setSearch] = useState("");
  const [positionGroup, setPositionGroup] = useState<string>("All");
  const [visibleKeys, setVisibleKeys] = useState<string[]>(DEFAULT_VISIBLE_KEYS);
  const [filters, setFilters] = useState<StatFilter[]>([]);
  const [nextFilterId, setNextFilterId] = useState(0);

  const visibleStats = useMemo(
    () => STATS.filter((s) => visibleKeys.includes(s.key)),
    [visibleKeys]
  );

  const filtered = useMemo(() => {
    let rows = players;

    // percentile mode: ineligible players (< 90 min) have nothing to show
    // in this mode, so they drop out entirely rather than showing blanks
    if (mode === "percentile") {
      rows = rows.filter((p) => p.percentile_eligible === true);
    }

    if (positionGroup !== "All") {
      rows = rows.filter((p) => p.position_group === positionGroup);
    }

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter(
        (p) =>
          String(p.player).toLowerCase().includes(q) ||
          String(p.team).toLowerCase().includes(q)
      );
    }

    for (const f of filters) {
      const stat = STATS.find((s) => s.key === f.statKey);
      if (!stat) continue;
      rows = rows.filter((p) => {
        const v = getStatValue(stat, p, mode, Number(p.minutes_played));
        if (v == null) return false;
        return f.operator === ">=" ? v >= f.value : v <= f.value;
      });
    }

    return rows;
  }, [mode, search, positionGroup, filters]);

  function addFilter() {
    setFilters((fs) => [
      ...fs,
      { id: nextFilterId, statKey: STATS[0].key, operator: ">=", value: 0 },
    ]);
    setNextFilterId((n) => n + 1);
  }

  function updateFilter(id: number, patch: Partial<StatFilter>) {
    setFilters((fs) => fs.map((f) => (f.id === id ? { ...f, ...patch } : f)));
  }

  function removeFilter(id: number) {
    setFilters((fs) => fs.filter((f) => f.id !== id));
  }

  function toggleColumn(key: string) {
    setVisibleKeys((keys) =>
      keys.includes(key) ? keys.filter((k) => k !== key) : [...keys, key]
    );
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h1 className="text-2xl font-bold mb-4">Players — 2022 World Cup</h1>

      {/* Mode toggle */}
      <div className="flex gap-2 mb-4">
        {(["total", "per90", "percentile"] as StatMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-3 py-1.5 rounded-md text-sm font-medium ${
              mode === m
                ? "bg-emerald-500 text-slate-950"
                : "bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            {m === "total" ? "Totals" : m === "per90" ? "Per 90" : "Percentile"}
          </button>
        ))}
      </div>

      {/* Search + position filter */}
      <div className="flex gap-3 mb-4">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search player or team..."
          className="bg-slate-900 border border-slate-700 rounded-md px-3 py-1.5 text-sm w-64"
        />
        <select
          value={positionGroup}
          onChange={(e) => setPositionGroup(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded-md px-3 py-1.5 text-sm"
        >
          <option value="All">All Positions</option>
          {POSITION_GROUPS.map((pg) => (
            <option key={pg} value={pg}>
              {pg}
            </option>
          ))}
        </select>
      </div>

      {/* Column picker, grouped by category */}
      <details className="mb-4 bg-slate-900 border border-slate-800 rounded-md p-3">
        <summary className="cursor-pointer text-sm text-slate-300 font-medium">
          Choose columns
        </summary>
        <div className="mt-3 grid grid-cols-2 md:grid-cols-3 gap-2">
          {Object.entries(CATEGORY_LABELS).map(([cat, label]) => (
            <div key={cat}>
              <div className="text-xs uppercase text-slate-500 mb-1">{label}</div>
              {STATS.filter((s) => s.category === cat).map((s) => (
                <label key={s.key} className="flex items-center gap-2 text-sm py-0.5">
                  <input
                    type="checkbox"
                    checked={visibleKeys.includes(s.key)}
                    onChange={() => toggleColumn(s.key)}
                  />
                  {s.label}
                </label>
              ))}
            </div>
          ))}
        </div>
      </details>

      {/* Filter builder */}
      <div className="mb-4 bg-slate-900 border border-slate-800 rounded-md p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-slate-300 font-medium">
            Filters {mode === "percentile" ? "(percentile 0-100)" : ""}
          </span>
          <button
            onClick={addFilter}
            className="text-xs bg-slate-800 hover:bg-slate-700 px-2 py-1 rounded"
          >
            + Add filter
          </button>
        </div>
        {filters.map((f) => (
          <div key={f.id} className="flex gap-2 items-center mb-2 text-sm">
            <select
              value={f.statKey}
              onChange={(e) => updateFilter(f.id, { statKey: e.target.value })}
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1"
            >
              {STATS.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
            <select
              value={f.operator}
              onChange={(e) =>
                updateFilter(f.id, { operator: e.target.value as Operator })
              }
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1"
            >
              <option value=">=">≥</option>
              <option value="<=">≤</option>
            </select>
            <input
              type="number"
              value={f.value}
              onChange={(e) =>
                updateFilter(f.id, { value: Number(e.target.value) })
              }
              className="bg-slate-800 border border-slate-700 rounded px-2 py-1 w-24"
            />
            <button
              onClick={() => removeFilter(f.id)}
              className="text-slate-500 hover:text-red-400"
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="text-sm text-slate-400 mb-2">
        {filtered.length} player{filtered.length !== 1 ? "s" : ""}
      </div>

      {/* Table */}
      <div className="overflow-x-auto border border-slate-800 rounded-md">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="text-left px-3 py-2">Player</th>
              <th className="text-left px-3 py-2">Team</th>
              <th className="text-left px-3 py-2">Position</th>
              <th className="text-right px-3 py-2">Min</th>
              {visibleStats.map((s) => (
                <th key={s.key} className="text-right px-3 py-2">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => (
              <tr
                key={String(p.player) + p.team}
                className="border-t border-slate-800 hover:bg-slate-900/50"
              >
                <td className="px-3 py-2">{p.player}</td>
                <td className="px-3 py-2 text-slate-400">{p.team}</td>
                <td className="px-3 py-2 text-slate-400">{p.primary_position}</td>
                <td className="px-3 py-2 text-right text-slate-400">
                  {Math.round(Number(p.minutes_played))}
                </td>
                {visibleStats.map((s) => (
                  <td key={s.key} className="px-3 py-2 text-right">
                    {formatValue(
                      s,
                      getStatValue(s, p, mode, Number(p.minutes_played)),
                      mode
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
