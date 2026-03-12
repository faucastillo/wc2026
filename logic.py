"""Business logic for the World Cup 2026 Simulator.

Contains the FIFA tie-breaking rules for group standings, the best
third-placed teams calculation, and the dynamic knockout bracket builder.
"""

import json
import logging
from pathlib import Path
from collections import defaultdict
from models import KnockoutMatch, Group, Tournament, Team
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

# Cache thirds_map.json at import time (read once, not on every request)
_THIRDS_MAP_CACHE: dict = {}


def _load_thirds_map() -> dict:
    """Load and cache the thirds mapping from the JSON file."""
    global _THIRDS_MAP_CACHE
    if not _THIRDS_MAP_CACHE:
        file_path = BASE_DIR / 'fixture_data' / 'thirds_map.json'
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                _THIRDS_MAP_CACHE = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error("Failed to load thirds_map.json: %s", e)
            _THIRDS_MAP_CACHE = {}
    return _THIRDS_MAP_CACHE


class GroupStandingsCalculator:
    """Handles the FIFA tie-breaking rules for group stage standings."""

    @staticmethod
    def calculate_standings(group: Group) -> list[Team]:
        for team in group.teams:
            team.reset_stats()

        for match in group.matches:
            if match.is_played:
                match.team_a.update_stats(match.goals_a, match.goals_b)
                match.team_b.update_stats(match.goals_b, match.goals_a)

        teams_by_pts = sorted(group.teams, key=lambda t: t.pts, reverse=True)
        return GroupStandingsCalculator._resolve_ties(teams_by_pts, group)

    @staticmethod
    def _resolve_ties(teams: list[Team], group: Group) -> list[Team]:
        buckets = defaultdict(list)
        for team in teams:
            buckets[team.pts].append(team)

        final_standings = []
        for _, tied_teams in sorted(buckets.items(), reverse=True):
            if len(tied_teams) == 1:
                final_standings.append(tied_teams[0])
            else:
                h2h_results = GroupStandingsCalculator._calculate_h2h(tied_teams, group)
                tied_teams.sort(key=lambda t: (
                    h2h_results[t]['pts'],
                    h2h_results[t]['gd'],
                    h2h_results[t]['gf'],
                    t.gd,
                    t.gf
                ), reverse=True)
                final_standings.extend(tied_teams)

        return final_standings

    @staticmethod
    def _calculate_h2h(tied_teams: list[Team], group: Group) -> dict:
        stats = {t: {'pts': 0, 'gd': 0, 'gf': 0, 'ga': 0} for t in tied_teams}
        for match in group.matches:
            if match.is_played and (match.team_a in tied_teams and match.team_b in tied_teams):
                if match.goals_a > match.goals_b:
                    stats[match.team_a]['pts'] += 3
                elif match.goals_a < match.goals_b:
                    stats[match.team_b]['pts'] += 3
                else:
                    stats[match.team_a]['pts'] += 1
                    stats[match.team_b]['pts'] += 1

                stats[match.team_a]['gd'] += match.goals_a - match.goals_b
                stats[match.team_a]['gf'] += match.goals_a
                stats[match.team_b]['gd'] += match.goals_b - match.goals_a
                stats[match.team_b]['gf'] += match.goals_b
        return stats


class TournamentCalculator:
    """Manages tournament-wide calculations."""

    @staticmethod
    def calculate_group_stage(tournament: Tournament) -> None:
        for group in tournament.groups.values():
            group.teams = GroupStandingsCalculator.calculate_standings(group)

    @staticmethod
    def get_best_third_teams(tournament: Tournament, num_teams: int = 8) -> list[Team]:
        third_teams = []
        for group in tournament.groups.values():
            if len(group.teams) >= 3:
                third_teams.append(group.teams[2])

        third_teams.sort(key=lambda t: (t.pts, t.gd, t.gf), reverse=True)
        return third_teams[:num_teams]


class KnockoutStageBuilder:
    """Dynamically builds the knockout bracket by parsing FIFA match labels."""

    @staticmethod
    def _resolve_thirds_mapping(best_thirds: list[Team]) -> dict:
        group_ids = sorted([t.group_id for t in best_thirds if t.group_id])
        thirds_key = "".join(group_ids)
        thirds_map = _load_thirds_map()
        mapping = thirds_map.get(thirds_key, {})
        if not mapping and thirds_key:
            logger.warning("No thirds mapping found for key: %s", thirds_key)
        return mapping

    @staticmethod
    def _resolve_team(tournament: Tournament, code: str, opponent_code: str, thirds_mapping: dict) -> Optional[Team]:
        if code.startswith('3'):
            target_code = thirds_mapping.get(opponent_code)
            if not target_code:
                logger.debug("No thirds mapping for opponent code: %s", opponent_code)
                return None
            return tournament.get_team_by_code(target_code)
        elif code != 'TBD':
            return tournament.get_team_by_code(code)
        return None

    @staticmethod
    def build_knockout_stage(tournament: Tournament, best_thirds: list[Team]) -> None:
        thirds_mapping = KnockoutStageBuilder._resolve_thirds_mapping(best_thirds)

        if not tournament.bracket:
            tournament.bracket = [KnockoutMatch(None, None, match_id=str(i)) for i in range(73, 105)]

        bracket_dict = {str(m.match_id): m for m in tournament.bracket}
        schedule = tournament.knockout_schedule

        for match_id in range(73, 105):
            match_id_str = str(match_id)
            match = bracket_dict[match_id_str]
            sched = schedule.get(match_id_str, {})
            label = sched.get('label', 'TBD vs TBD')

            parts = label.split(' vs ')
            code_a = parts[0].strip() if len(parts) > 0 else 'TBD'
            code_b = parts[1].strip() if len(parts) > 1 else 'TBD'

            # Round of 32: resolve directly from group positions + thirds mapping
            if 73 <= match_id <= 88:
                match.team_a = KnockoutStageBuilder._resolve_team(tournament, code_a, code_b, thirds_mapping)
                match.team_b = KnockoutStageBuilder._resolve_team(tournament, code_b, code_a, thirds_mapping)
            else:
                # Later rounds: wire up bracket links from previous matches
                if code_a.startswith('W'):
                    prev_match = bracket_dict.get(code_a[1:])
                    if prev_match:
                        prev_match.next_match = match
                        prev_match.next_match_slot = 'a'
                        if prev_match.is_played:
                            match.team_a = prev_match.get_winner()
                elif code_a.startswith('RU'):
                    prev_match = bracket_dict.get(code_a[2:])
                    if prev_match:
                        prev_match.third_place_match = match
                        if prev_match.is_played:
                            match.team_a = prev_match.get_loser()

                if code_b.startswith('W'):
                    prev_id = '96' if code_b[1:] == '100' else code_b[1:]  # Fix FIFA JSON typo
                    prev_match = bracket_dict.get(prev_id)
                    if prev_match:
                        prev_match.next_match = match
                        prev_match.next_match_slot = 'b'
                        if prev_match.is_played:
                            match.team_b = prev_match.get_winner()
                elif code_b.startswith('RU'):
                    prev_match = bracket_dict.get(code_b[2:])
                    if prev_match:
                        prev_match.third_place_match = match
                        if prev_match.is_played:
                            match.team_b = prev_match.get_loser()

        tournament.bracket = [bracket_dict[str(i)] for i in range(73, 105)]