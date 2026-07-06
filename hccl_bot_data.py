"""Data access helpers for the HCCL Telegram Bot.

The bot reads the same Supabase tables created by HCCL Dashboard v3:
- hccl_snapshots
- hccl_rankings
- hccl_weekly_report
- hccl_team_rankings
- hccl_benchmarks

Required environment variables:
    SUPABASE_URL
    SUPABASE_KEY
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from supabase import create_client


CATEGORY_ALIASES = {
    "bat": "Batting",
    "batting": "Batting",
    "batsman": "Batting",
    "batter": "Batting",
    "bowl": "Bowling",
    "bowling": "Bowling",
    "bowler": "Bowling",
    "all": "All-Rounder",
    "ar": "All-Rounder",
    "allrounder": "All-Rounder",
    "all-rounder": "All-Rounder",
    "all_rounder": "All-Rounder",
}

VALID_CATEGORIES = ["Batting", "Bowling", "All-Rounder"]

BENCHMARK_LABELS = {
    "highest_runs": "Highest Runs",
    "highest_bat_avg": "Highest Batting Average",
    "highest_sr": "Highest Strike Rate",
    "highest_rap": "Highest RAP",
    "highest_wickets": "Highest Wickets",
    "best_bowl_avg": "Best Bowling Average",
    "best_eco": "Best Economy",
    "best_bsr": "Best Bowling Strike Rate",
    "highest_bap": "Highest BAP",
}


class HCCLBotError(Exception):
    """Friendly bot/database error."""


def normalize_text(value: Any) -> str:
    """Normalize names/teams for forgiving searches."""
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def parse_category(value: str, default: str = "Batting") -> str:
    key = normalize_text(value)
    return CATEGORY_ALIASES.get(key, default)


def env_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HCCLBotError(f"Missing environment variable: {name}")
    return value


def get_supabase_client():
    return create_client(env_required("SUPABASE_URL"), env_required("SUPABASE_KEY"))


def format_rating(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except Exception:
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def format_rank(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"#{int(value)}"
    except Exception:
        return str(value)


def clean_change(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def format_player_line(row: Dict[str, Any], include_category: bool = False) -> str:
    category = f"{row.get('category')} " if include_category else ""
    rank = format_rank(row.get("rank"))
    movement = row.get("movement") or "—"
    player = row.get("player") or "Unknown"
    team = row.get("team") or "—"
    rating = format_rating(row.get("rating"))
    change = clean_change(row.get("rating_change"))
    return f"{rank}. {category}{player} ({team}) — {rating} pts {movement} | {change}"


@dataclass
class Snapshot:
    id: str
    week_label: str
    snapshot_date: str
    official_only: bool
    notes: str = ""

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Snapshot":
        return cls(
            id=str(row.get("id")),
            week_label=str(row.get("week_label") or "Saved Rankings"),
            snapshot_date=str(row.get("snapshot_date") or ""),
            official_only=bool(row.get("official_only")),
            notes=str(row.get("notes") or ""),
        )


class HCCLSupabaseStore:
    def __init__(self):
        self.client = get_supabase_client()

    def list_snapshots(self, limit: int = 10) -> List[Snapshot]:
        response = (
            self.client.table("hccl_snapshots")
            .select("id,week_label,snapshot_date,official_only,notes,created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [Snapshot.from_row(row) for row in (response.data or [])]

    def latest_snapshot(self) -> Snapshot:
        snapshots = self.list_snapshots(limit=1)
        if not snapshots:
            raise HCCLBotError("No saved HCCL ranking snapshots found in Supabase yet.")
        return snapshots[0]

    def _snapshot_id(self, snapshot_id: Optional[str] = None) -> str:
        return snapshot_id or self.latest_snapshot().id

    def rankings(self, category: Optional[str] = None, limit: Optional[int] = None, snapshot_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = (
            self.client.table("hccl_rankings")
            .select("category,rank,movement,player,team,rating,previous_rank,previous_rating,rating_change,status")
            .eq("snapshot_id", self._snapshot_id(snapshot_id))
        )
        if category:
            query = query.eq("category", category)
        query = query.order("rank")
        if limit:
            query = query.limit(limit)
        response = query.execute()
        return response.data or []

    def top(self, category: str, limit: int = 10) -> Tuple[Snapshot, List[Dict[str, Any]]]:
        snapshot = self.latest_snapshot()
        return snapshot, self.rankings(category=category, limit=limit, snapshot_id=snapshot.id)

    def all_rankings_latest(self) -> Tuple[Snapshot, List[Dict[str, Any]]]:
        snapshot = self.latest_snapshot()
        rows: List[Dict[str, Any]] = []
        for category in VALID_CATEGORIES:
            rows.extend(self.rankings(category=category, snapshot_id=snapshot.id))
        return snapshot, rows

    def find_player(self, query_text: str) -> Tuple[Snapshot, Optional[str], List[Dict[str, Any]], List[str]]:
        snapshot, rows = self.all_rankings_latest()
        target = normalize_text(query_text)
        if not target:
            return snapshot, None, [], []

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        display_names: Dict[str, str] = {}
        for row in rows:
            name = str(row.get("player") or "").strip()
            key = normalize_text(name)
            if not key:
                continue
            grouped.setdefault(key, []).append(row)
            display_names[key] = name

        # Exact normalized match first.
        if target in grouped:
            return snapshot, display_names[target], sorted(grouped[target], key=lambda r: VALID_CATEGORIES.index(r["category"])), []

        # Then partial contains match.
        matches = [key for key in grouped if target in key or key in target]
        if len(matches) == 1:
            key = matches[0]
            return snapshot, display_names[key], sorted(grouped[key], key=lambda r: VALID_CATEGORIES.index(r["category"])), []

        suggestions = [display_names[key] for key in matches[:8]]
        return snapshot, None, [], suggestions

    def team_rankings(self, team_query: str, category: Optional[str] = None, per_category_limit: int = 10) -> Tuple[Snapshot, Optional[str], List[Dict[str, Any]], List[str]]:
        snapshot = self.latest_snapshot()
        response = (
            self.client.table("hccl_team_rankings")
            .select("team,category,team_rank,overall_rank,movement,player,rating,previous_rating,rating_change,status")
            .eq("snapshot_id", snapshot.id)
            .order("category")
            .order("team_rank")
            .execute()
        )
        rows = response.data or []
        target = normalize_text(team_query)
        teams: Dict[str, str] = {}
        for row in rows:
            team = str(row.get("team") or "").strip()
            if team:
                teams[normalize_text(team)] = team

        if not target:
            return snapshot, None, [], sorted(set(teams.values()))[:12]

        chosen_key: Optional[str] = None
        if target in teams:
            chosen_key = target
        else:
            matches = [key for key in teams if target in key or key in target]
            if len(matches) == 1:
                chosen_key = matches[0]
            elif len(matches) > 1:
                return snapshot, None, [], [teams[key] for key in matches[:8]]

        if not chosen_key:
            return snapshot, None, [], sorted(set(teams.values()))[:12]

        chosen_team = teams[chosen_key]
        selected = [r for r in rows if normalize_text(r.get("team")) == chosen_key]
        if category:
            selected = [r for r in selected if r.get("category") == category]
        selected = selected[: per_category_limit if category else per_category_limit * 3]
        return snapshot, chosen_team, selected, []

    def weekly_report(self, section: Optional[str] = None, limit: int = 20) -> Tuple[Snapshot, List[Dict[str, Any]]]:
        snapshot = self.latest_snapshot()
        query = (
            self.client.table("hccl_weekly_report")
            .select("category,report_section,player,team,current_rank,previous_rank,movement,current_rating,previous_rating,rating_change,status")
            .eq("snapshot_id", snapshot.id)
            .order("category")
            .order("report_section")
        )
        if section:
            query = query.eq("report_section", section)
        response = query.limit(limit).execute()
        return snapshot, response.data or []

    def benchmarks(self) -> Tuple[Snapshot, List[Dict[str, Any]]]:
        snapshot = self.latest_snapshot()
        response = (
            self.client.table("hccl_benchmarks")
            .select("benchmark_key,benchmark_value")
            .eq("snapshot_id", snapshot.id)
            .order("benchmark_key")
            .execute()
        )
        return snapshot, response.data or []
