"""Domain models for the World Cup 2026 Simulator.

Defines Team, Match, KnockoutMatch, Group, and Tournament classes
that manage team statistics, match results, and bracket progression.
"""

from typing import Optional


class Team:
    """Represents a national team in the tournament."""
    
    def __init__(self, name: str):
        self.name: str = name
        self.id: Optional[str] = None
        self.iso_code: str = ""
        self.is_placeholder: bool = False
        self.group_id: Optional[str] = None
        self.pts: int = 0
        self.gf: int = 0
        self.ga: int = 0
        self.gd: int = 0
        self.wins: int = 0
        self.draws: int = 0
        self.losses: int = 0
        self.yellow_cards: int = 0
        self.red_cards: int = 0

    def reset_stats(self) -> None:
        """Resets all tournament statistics for the team."""
        self.pts = self.gf = self.ga = self.gd = 0
        self.wins = self.draws = self.losses = 0
        self.yellow_cards = self.red_cards = 0

    def update_stats(self, goals_for: int, goals_against: int, yellow_cards: int = 0, red_cards: int = 0) -> None:
        """Updates team statistics based on a match result."""
        self.gf += goals_for
        self.ga += goals_against
        self.gd = self.gf - self.ga
        self.yellow_cards += yellow_cards
        self.red_cards += red_cards

        if goals_for > goals_against:
            self.pts += 3
            self.wins += 1
        elif goals_for == goals_against:
            self.pts += 1
            self.draws += 1
        else:
            self.losses += 1

    def __repr__(self) -> str:
        return f"<{self.name} | Pts: {self.pts} | GD: {self.gd}>"


class Match:
    """Represents a single football match."""
    
    def __init__(self, team_a: Optional[Team], team_b: Optional[Team], match_id: Optional[str] = None, 
                 date_str: str = "TBD", time_str: str = "TBD", stadium: str = "TBD", 
                 city: str = "TBD", country: str = "TBD"):
        self.match_id = match_id
        self.team_a = team_a
        self.team_b = team_b
        self.goals_a: Optional[int] = None
        self.goals_b: Optional[int] = None
        self.is_played: bool = False
        
        self.date_str = date_str
        self.time_str = time_str
        self.stadium = stadium
        self.city = city
        self.country = country

    def set_scores(self, goals_a: int, goals_b: int) -> None:
        """Sets the final score and marks the match as played."""
        self.goals_a = goals_a
        self.goals_b = goals_b
        self.is_played = True

    def reset(self) -> None:
        """Clears the match result."""
        self.goals_a = None
        self.goals_b = None
        self.is_played = False




class KnockoutMatch(Match):
    """Represents a knockout stage match with penalty resolution and bracket propagation."""
    
    def __init__(self, team_a: Optional[Team], team_b: Optional[Team], match_id: Optional[str] = None, 
                 date_str: str = "TBD", time_str: str = "TBD"):
        super().__init__(team_a, team_b, match_id, date_str, time_str)
        self.penalties_a: int = 0
        self.penalties_b: int = 0
        self.next_match: Optional['KnockoutMatch'] = None
        self.next_match_slot: Optional[str] = None
        self.third_place_match: Optional['KnockoutMatch'] = None

    def reset(self) -> None:
        """Clears match result including penalty scores."""
        super().reset()
        self.penalties_a = 0
        self.penalties_b = 0

    def set_scores(self, goals_a: int, goals_b: int, pen_a: int = 0, pen_b: int = 0) -> None:
        super().set_scores(goals_a, goals_b)
        self.penalties_a = pen_a
        self.penalties_b = pen_b
        self.propagate_result()

    def get_winner(self) -> Optional[Team]:
        if not self.is_played: return None
        if self.goals_a > self.goals_b: return self.team_a
        if self.goals_b > self.goals_a: return self.team_b
        if self.penalties_a > self.penalties_b: return self.team_a
        if self.penalties_b > self.penalties_a: return self.team_b
        return None
            
    def get_loser(self) -> Optional[Team]:
        if not self.is_played: return None
        if self.goals_a > self.goals_b: return self.team_b
        if self.goals_b > self.goals_a: return self.team_a
        if self.penalties_a > self.penalties_b: return self.team_b
        if self.penalties_b > self.penalties_a: return self.team_a
        return None

    def propagate_result(self) -> None:
        """Pushes the winner/loser to subsequent bracket stages."""
        winner = self.get_winner()
        loser = self.get_loser()

        if self.next_match and winner:
            if self.next_match_slot == 'a':
                self.next_match.team_a = winner
            elif self.next_match_slot == 'b':
                self.next_match.team_b = winner

        if self.third_place_match and loser:
            if str(self.match_id) == '101':  # Semi-final 1
                self.third_place_match.team_a = loser
            elif str(self.match_id) == '102':  # Semi-final 2
                self.third_place_match.team_b = loser


class Group:
    """Represents a tournament group."""
    
    def __init__(self, name: str):
        self.name = name
        self.teams: list[Team] = []
        self.matches: list[Match] = []

    def add_team(self, team: Team) -> None:
        self.teams.append(team)
        team.group_id = self.name

    def create_match(self, team_a: Team, team_b: Team) -> Match:
        match = Match(team_a, team_b)
        self.matches.append(match)
        return match


class Tournament:
    """Main container for the World Cup data."""
    
    def __init__(self, name: str):
        self.name = name
        self.groups: dict[str, Group] = {}
        self.bracket: list[KnockoutMatch] = []
        self.knockout_schedule: dict = {}

    def add_group(self, group: Group) -> None:
        self.groups[group.name] = group

    def get_team_by_code(self, code: str) -> Optional[Team]:
        """Finds a team by its placement code (e.g., '1A')."""
        try:
            pos_idx = int(code[0]) - 1
            group_char = code[1:]
            group = self.groups.get(group_char)
            if group and len(group.teams) > pos_idx:
                return group.teams[pos_idx]
        except (ValueError, IndexError):
            pass
        return None