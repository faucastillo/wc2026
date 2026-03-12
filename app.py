"""Flask application for the World Cup 2026 Simulator.

Handles routing, initial data loading, and REST API endpoints for
real-time synchronization of match results and tournament state.

Note: Tournament state is stored in module-level globals. This means
the app is designed for single-user use. In a multi-user production
environment, you would need session-based state or a database.
"""

import csv
import json
import os
import random
import logging
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify

from models import Team, Group, Tournament, KnockoutMatch
from logic import TournamentCalculator, KnockoutStageBuilder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__)

# Global Tournament Instance
wc_2026 = Tournament("World Cup 2026")

# --- Playoff Configuration (Teams and ISO 3166-1 Codes) ---
PLAYOFF_CONFIG = {
    "6": {
        "name": "UEFA Path A",
        "matches": ["Italy vs N. Ireland", "Wales vs Bosnia"],
        "candidates": [
            {"name": "Italy", "iso": "it"},
            {"name": "Northern Ireland", "iso": "gb-nir"},
            {"name": "Wales", "iso": "gb-wls"},
            {"name": "Bosnia & Herz.", "iso": "ba"}
        ]
    },
    "23": {
        "name": "UEFA Path B",
        "matches": ["Ukraine vs Sweden", "Poland vs Albania"],
        "candidates": [
            {"name": "Ukraine", "iso": "ua"},
            {"name": "Sweden", "iso": "se"},
            {"name": "Poland", "iso": "pl"},
            {"name": "Albania", "iso": "al"}
        ]
    },
    "16": {
        "name": "UEFA Path C",
        "matches": ["Turkey vs Romania", "Slovakia vs Kosovo"],
        "candidates": [
            {"name": "Turkey", "iso": "tr"},
            {"name": "Romania", "iso": "ro"},
            {"name": "Slovakia", "iso": "sk"},
            {"name": "Kosovo", "iso": "xk"}
        ]
    },
    "4": {
        "name": "UEFA Path D",
        "matches": ["Denmark vs N. Macedonia", "Czechia vs Ireland"],
        "candidates": [
            {"name": "Denmark", "iso": "dk"},
            {"name": "North Macedonia", "iso": "mk"},
            {"name": "Czechia", "iso": "cz"},
            {"name": "Ireland", "iso": "ie"}
        ]
    },
    "42": {
        "name": "FIFA Play-off 1",
        "matches": ["New Caledonia vs Jamaica", "Winner vs DR Congo"],
        "candidates": [
            {"name": "DR Congo", "iso": "cd"},
            {"name": "Jamaica", "iso": "jm"},
            {"name": "New Caledonia", "iso": "nc"}
        ]
    },
    "35": {
        "name": "FIFA Play-off 2",
        "matches": ["Bolivia vs Suriname", "Winner vs Iraq"],
        "candidates": [
            {"name": "Iraq", "iso": "iq"},
            {"name": "Bolivia", "iso": "bo"},
            {"name": "Suriname", "iso": "sr"}
        ]
    }
}

playoff_winners_state = {}


def init_tournament_data(tournament: Tournament) -> None:
    """Load teams, matches, and venue data from CSV files into the tournament."""
    cities, teams_by_id = {}, {}

    with open(BASE_DIR / 'fixture_data' / 'host_cities.csv', 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            cities[row['id']] = {
                'stadium': row['venue_name'],
                'city': row['city_name'],
                'country': row['country']
            }

    with open(BASE_DIR / 'fixture_data' / 'teams.csv', 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            team = Team(row['team_name'])
            team.iso_code = row.get('iso_code', '').lower()
            team.id = row['id']
            team.is_placeholder = row.get('is_placeholder') == 'True'
            teams_by_id[row['id']] = team

            if row['group_letter'] not in tournament.groups:
                tournament.add_group(Group(row['group_letter']))
            tournament.groups[row['group_letter']].add_team(team)

    tournament.knockout_schedule = {}
    tournament.bracket = []

    with open(BASE_DIR / 'fixture_data' / 'matches.csv', 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            dt = datetime.strptime(row['kickoff_at'].split(' ')[0], "%Y-%m-%d")
            time_str = row['kickoff_at'].split(' ')[1].split('-')[0].split('+')[0][:5]
            city_info = cities.get(row['city_id'], {})

            if row['stage_id'] == '1':
                # Group stage match
                home = teams_by_id.get(row['home_team_id'])
                away = teams_by_id.get(row['away_team_id'])
                if not home or not away:
                    continue

                match = tournament.groups[home.group_id].create_match(home, away)
                match.match_id = row['match_number']
                match.date_str = dt.strftime("%d %b %Y")
                match.time_str = time_str
                match.stadium = city_info.get('stadium', 'TBD')
                match.city = city_info.get('city', 'TBD')
                match.country = city_info.get('country', 'TBD')
            else:
                # Knockout stage match
                tournament.knockout_schedule[row['match_number']] = {
                    "date": dt.strftime("%d %b %Y"),
                    "time": time_str,
                    "stadium": city_info.get('stadium', 'TBD'),
                    "city": city_info.get('city', 'TBD'),
                    "country": city_info.get('country', 'TBD'),
                    "label": row['match_label']
                }
                match = KnockoutMatch(
                    None, None, match_id=row['match_number'],
                    date_str=dt.strftime("%d %b %Y"), time_str=time_str
                )
                match.stadium = city_info.get('stadium', 'TBD')
                match.city = city_info.get('city', 'TBD')
                match.country = city_info.get('country', 'TBD')
                match.label = row['match_label']
                tournament.bracket.append(match)


init_tournament_data(wc_2026)


def load_real_results() -> dict:
    """Load official match results and playoff winners from real_results.json.
    
    Returns a dict with 'scores' (match_id -> score data) and
    'playoff_winners' (placeholder_id -> winner data) keys.
    """
    file_path = BASE_DIR / 'fixture_data' / 'real_results.json'
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Support both old flat format and new structured format
            if 'scores' in data:
                return data
            return {'scores': data, 'playoff_winners': {}}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load real_results.json: %s", e)
        return {'scores': {}, 'playoff_winners': {}}


def get_realistic_goals() -> int:
    """Generate a realistic goal count using weighted Poisson-like distribution."""
    return random.choices(
        population=[0, 1, 2, 3, 4, 5],
        weights=[28, 35, 23, 10, 3, 1],
        k=1
    )[0]


def get_realistic_penalties() -> tuple:
    """Generate realistic penalty shootout scores with weighted outcomes."""
    outcomes = [
        (3, 0), (0, 3), (3, 1), (1, 3), (4, 2), (2, 4),
        (4, 3), (3, 4), (5, 3), (3, 5), (5, 4), (4, 5),
        (6, 5), (5, 6), (7, 6), (6, 7), (8, 7), (7, 8)
    ]
    weights = [
        1, 1, 3, 3, 5, 5, 6, 6, 5, 5, 6, 6, 3, 3, 2, 2, 1, 1
    ]
    return random.choices(outcomes, weights=weights, k=1)[0]


def reset_all_matches(matches: list) -> None:
    """Reset all matches in the given list."""
    for m in matches:
        m.reset()


def get_resolved_team_data(team: Team) -> tuple[str, str]:
    """Return the display name and ISO code for a team, resolving playoff placeholders."""
    if team.is_placeholder and team.id in playoff_winners_state:
        winner = playoff_winners_state[team.id]
        return winner['name'], winner['iso']
    return team.name, team.iso_code


def apply_real_results(tournament: Tournament) -> None:
    """Apply official match results and playoff winners from real_results.json.
    
    Ensures real results are always reflected in the tournament state,
    even after resets or randomization.
    """
    real_data = load_real_results()
    real_scores = real_data.get('scores', {})
    real_playoffs = real_data.get('playoff_winners', {})

    if not real_scores and not real_playoffs:
        return

    # Apply real match scores
    all_matches = [m for g in tournament.groups.values() for m in g.matches] + tournament.bracket
    match_dict = {str(m.match_id): m for m in all_matches}

    for m_id, scores in real_scores.items():
        if match := match_dict.get(str(m_id)):
            try:
                ga, gb = int(scores['a']), int(scores['b'])
                if isinstance(match, KnockoutMatch):
                    pa = int(scores.get('pen_a', 0))
                    pb = int(scores.get('pen_b', 0))
                    match.set_scores(ga, gb, pa, pb)
                else:
                    match.set_scores(ga, gb)
            except (ValueError, TypeError, KeyError):
                logger.warning("Invalid real result for match %s: %s", m_id, scores)

    # Apply real playoff winners
    for pid, winner in real_playoffs.items():
        playoff_winners_state[pid] = winner


def get_tournament_state(tournament: Tournament, lock_enabled: bool = True) -> dict:
    """Calculate and return the complete tournament state as a JSON-serializable dict."""
    # Apply real results only when lock is enabled
    if lock_enabled:
        apply_real_results(tournament)

    TournamentCalculator.calculate_group_stage(tournament)
    best_thirds = TournamentCalculator.get_best_third_teams(tournament, num_teams=8)
    KnockoutStageBuilder.build_knockout_stage(tournament, best_thirds)

    standings = {}
    group_matches_data = {}

    for letter, group in tournament.groups.items():
        group_data = []
        matches_list = []
        has_placeholder = False
        placeholder_id = None

        for t in group.teams:
            d_name, d_iso = get_resolved_team_data(t)
            if t.is_placeholder:
                has_placeholder = True
                placeholder_id = t.id
            group_data.append({
                "name": d_name, "pts": t.pts, "p": t.wins + t.draws + t.losses,
                "w": t.wins, "d": t.draws, "l": t.losses,
                "gd": t.gd, "gf": t.gf, "ga": t.ga, "iso_code": d_iso
            })

        for m in group.matches:
            name_a, iso_a = get_resolved_team_data(m.team_a)
            name_b, iso_b = get_resolved_team_data(m.team_b)
            matches_list.append({
                "match_id": str(m.match_id),
                "team_a": name_a, "iso_a": iso_a,
                "team_b": name_b, "iso_b": iso_b,
                "date_str": m.date_str, "time_str": m.time_str,
                "stadium": m.stadium, "city": m.city
            })

        standings[letter] = {"teams": group_data, "playoff_info": None}
        group_matches_data[letter] = matches_list

        if has_placeholder and placeholder_id in PLAYOFF_CONFIG:
            config = PLAYOFF_CONFIG[placeholder_id]
            standings[letter]["playoff_info"] = {
                "id": placeholder_id, "name": config["name"], "matches": config["matches"],
                "candidates": config["candidates"],
                "selected_iso": playoff_winners_state.get(placeholder_id, {}).get('iso')
            }

    thirds = []
    for i, t in enumerate(TournamentCalculator.get_best_third_teams(tournament, 12)):
        d_name, d_iso = get_resolved_team_data(t)
        thirds.append({
            "name": d_name, "group": getattr(t, 'group_id', '?'), "pts": t.pts,
            "p": t.wins + t.draws + t.losses, "gd": t.gd, "gf": t.gf,
            "is_qualified": i < 8, "iso_code": d_iso
        })

    knockouts = {"R32": [], "R16": [], "QF": [], "SF": [], "TP": [], "Final": []}
    for i, match in enumerate(tournament.bracket):
        sched = tournament.knockout_schedule.get(str(match.match_id), {})
        lbl = sched.get('label', 'TBD vs TBD').split(' vs ')

        t_a_name, t_a_iso = (
            get_resolved_team_data(match.team_a) if match.team_a
            else (f"TBD ({lbl[0] if len(lbl) > 0 else '?'})", "")
        )
        t_b_name, t_b_iso = (
            get_resolved_team_data(match.team_b) if match.team_b
            else (f"TBD ({lbl[1] if len(lbl) > 1 else '?'})", "")
        )

        phase = (
            "R32" if i < 16 else
            "R16" if i < 24 else
            "QF" if i < 28 else
            "SF" if i < 30 else
            "TP" if i == 30 else
            "Final"
        )
        knockouts[phase].append({
            "match_id": str(match.match_id), "team_a": t_a_name, "team_b": t_b_name,
            "goals_a": match.goals_a, "goals_b": match.goals_b,
            "pen_a": match.penalties_a, "pen_b": match.penalties_b,
            "iso_a": t_a_iso, "iso_b": t_b_iso,
            "date": sched.get("date", "TBD"), "time": sched.get("time", "TBD"),
            "stadium": sched.get("stadium", "TBD"), "city": sched.get("city", "TBD")
        })

    scores = {}
    for m in ([m for g in wc_2026.groups.values() for m in g.matches] + wc_2026.bracket):
        if m.is_played:
            s = {"a": m.goals_a, "b": m.goals_b}
            if isinstance(m, KnockoutMatch) and m.goals_a == m.goals_b:
                s["pen_a"] = m.penalties_a
                s["pen_b"] = m.penalties_b
            scores[str(m.match_id)] = s

    real_data = load_real_results()
    return {
        "status": "success", "standings": standings, "thirds": thirds,
        "knockouts": knockouts, "group_matches": group_matches_data,
        "scores": scores, "playoff_winners": playoff_winners_state,
        "real_results": real_data.get('scores', {}),
        "real_playoff_winners": real_data.get('playoff_winners', {})
    }


@app.route('/')
def index():
    """Render the main SPA page."""
    sorted_groups = {k: wc_2026.groups[k] for k in sorted(wc_2026.groups.keys())}
    return render_template('index.html', groups=sorted_groups)


@app.route('/api/sync', methods=['POST'])
def sync_state():
    """Synchronize match results from the frontend and recalculate tournament state."""
    data = request.json or {}
    lock = data.get('lock_enabled', True)

    if 'playoff_selection' in data:
        selection = data['playoff_selection']
        playoff_winners_state[selection['id']] = selection['winner']

    all_matches = [m for g in wc_2026.groups.values() for m in g.matches] + wc_2026.bracket
    real_data = load_real_results()
    real_ids = set(real_data.get('scores', {}).keys()) if lock else set()
    real_playoff_ids = set(real_data.get('playoff_winners', {}).keys()) if lock else set()

    if data.get('reset_all'):
        for m in all_matches:
            if str(m.match_id) not in real_ids:
                m.reset()
        # Clear playoff selections except locked ones
        for pid in list(playoff_winners_state.keys()):
            if pid not in real_playoff_ids:
                del playoff_winners_state[pid]
    elif data.get('reset_group'):
        if group := wc_2026.groups.get(data.get('reset_group')):
            for m in group.matches:
                if str(m.match_id) not in real_ids:
                    m.reset()
            # Also clear playoff selections for placeholder teams (skip locked)
            for team in group.teams:
                if team.is_placeholder and team.id in playoff_winners_state:
                    if team.id not in real_playoff_ids:
                        del playoff_winners_state[team.id]

    if scores_data := data.get('scores', {}):
        match_dict = {str(m.match_id): m for m in all_matches}
        for m_id, scores in scores_data.items():
            if match := match_dict.get(str(m_id)):
                if scores.get('a') == "" or scores.get('b') == "":
                    match.reset()
                else:
                    try:
                        ga, gb = int(scores['a']), int(scores['b'])
                        if isinstance(match, KnockoutMatch):
                            pa = int(scores.get('pen_a', 0))
                            pb = int(scores.get('pen_b', 0))
                            match.set_scores(ga, gb, pa, pb)
                        else:
                            match.set_scores(ga, gb)
                    except (ValueError, TypeError):
                        logger.warning("Invalid score data for match %s: %s", m_id, scores)
                        match.reset()

    return jsonify(get_tournament_state(wc_2026, lock_enabled=lock))


@app.route('/api/randomize', methods=['POST'])
def randomize_matches():
    """Simulate random results for the entire tournament."""
    data = request.json or {}
    lock = data.get('lock_enabled', True)

    # Load real results first so we know which playoffs/matches are locked
    real_data = load_real_results()
    real_ids = set(real_data.get('scores', {}).keys()) if lock else set()
    real_playoff_ids = set(real_data.get('playoff_winners', {}).keys()) if lock else set()

    # Randomly select playoff winners (skip locked ones)
    for pid, config in PLAYOFF_CONFIG.items():
        if pid not in real_playoff_ids:
            playoff_winners_state[pid] = random.choice(config['candidates'])

    # Simulate group stage (skip real results if locked)

    all_group_matches = [m for g in wc_2026.groups.values() for m in g.matches]
    for m in all_group_matches:
        if str(m.match_id) not in real_ids:
            m.set_scores(get_realistic_goals(), get_realistic_goals())

    TournamentCalculator.calculate_group_stage(wc_2026)
    best_thirds = TournamentCalculator.get_best_third_teams(wc_2026, 8)
    KnockoutStageBuilder.build_knockout_stage(wc_2026, best_thirds)

    # Simulate knockout stage (reset first to clear stale bracket data)
    for m in wc_2026.bracket:
        m.reset()

    KnockoutStageBuilder.build_knockout_stage(wc_2026, best_thirds)

    for m in wc_2026.bracket:
        if not m.team_a or not m.team_b:
            continue
        if str(m.match_id) in real_ids:
            continue
        ga, gb = get_realistic_goals(), get_realistic_goals()
        pa, pb = 0, 0
        if ga == gb:
            pa, pb = get_realistic_penalties()
        m.set_scores(ga, gb, pa, pb)

    return jsonify(get_tournament_state(wc_2026, lock_enabled=lock))


@app.route('/api/randomize_group', methods=['POST'])
def randomize_group():
    """Simulate random results for a single group."""
    data = request.json or {}
    group_letter = data.get('group')
    lock = data.get('lock_enabled', True)

    if not group_letter or group_letter not in wc_2026.groups:
        return jsonify({"status": "error", "message": "Invalid group"}), 400

    group = wc_2026.groups[group_letter]

    # Randomize all matches in this group (skip real results if locked)
    real_data = load_real_results()
    real_ids = set(real_data.get('scores', {}).keys()) if lock else set()
    real_playoff_ids = set(real_data.get('playoff_winners', {}).keys()) if lock else set()

    # If the group has a playoff placeholder, randomly select a winner (skip locked)
    for team in group.teams:
        if team.is_placeholder and team.id in PLAYOFF_CONFIG:
            if team.id not in real_playoff_ids:
                playoff_winners_state[team.id] = random.choice(PLAYOFF_CONFIG[team.id]['candidates'])
    for m in group.matches:
        if str(m.match_id) not in real_ids:
            m.set_scores(get_realistic_goals(), get_realistic_goals())

    return jsonify(get_tournament_state(wc_2026, lock_enabled=lock))


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))