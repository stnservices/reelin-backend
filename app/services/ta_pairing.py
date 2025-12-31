"""
Trout Area (TA) Pairing & Lineup Generation Service.

Supports multiple pairing algorithms for TA competitions:

1. ROUND_ROBIN_FULL - Extended format (N legs for N participants) - MORE matches
2. ROUND_ROBIN_HALF - Standard TA format (N/2 legs) - normal duration
3. ROUND_ROBIN_CUSTOM - Specify exact number of legs (1 to N)
4. SIMPLE_PAIRS - Basic n/2 single-leg pairing (fastest, for quick events)

Features:
- Ghost participant handling for odd numbers
- Adjacent seat matching (1-2, 3-4, 5-6, etc.) - side-by-side matches
- TA rotation algorithm: odd seat → -2, even seat → +2
- Special leg at middle with +4 rotation for even seats
- Visual schedule/map generation
- Seat rotation patterns between legs

TA Rotation Algorithm (from Django production code):
- Matches are ALWAYS between adjacent seats (side-by-side)
- Each leg, participants rotate using: odd seat → -2, even seat → +2
- Special leg at middle (total_legs/2 + 1) where even seats get +4
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math
import json


class PairingAlgorithm(str, Enum):
    """Available pairing algorithms for TA events."""
    ROUND_ROBIN_FULL = "round_robin_full"      # N legs - extended, MORE matches
    ROUND_ROBIN_HALF = "round_robin_half"      # N/2 legs - standard TA format
    ROUND_ROBIN_CUSTOM = "round_robin_custom"  # User-specified number of legs
    SIMPLE_PAIRS = "simple_pairs"              # Single leg pairing


@dataclass
class Participant:
    """Represents a participant in the pairing."""
    id: int
    user_id: Optional[int]
    enrollment_id: Optional[int]
    name: str
    is_ghost: bool = False

    def __repr__(self):
        if self.is_ghost:
            return f"GHOST-{self.id}"
        return f"P{self.id}"


@dataclass
class Match:
    """Represents a single match pairing."""
    round_number: int
    match_number: int
    seat_a: int
    seat_b: int
    participant_a: Participant
    participant_b: Participant
    is_ghost_match: bool = False
    ghost_side: Optional[str] = None  # 'A' or 'B'

    def __repr__(self):
        ghost_marker = " [GHOST]" if self.is_ghost_match else ""
        return f"R{self.round_number}M{self.match_number}: {self.participant_a} vs {self.participant_b}{ghost_marker}"


@dataclass
class PairingResult:
    """Complete result of pairing generation."""
    algorithm: PairingAlgorithm
    total_participants: int
    real_participants: int
    has_ghost: bool
    total_rounds: int
    matches_per_round: int
    total_matches: int
    matches_per_participant: int
    rounds: list[list[Match]] = field(default_factory=list)
    rotation_map: dict = field(default_factory=dict)
    participant_schedule: dict = field(default_factory=dict)  # {participant_id: [opponents]}

    def to_visual_schedule(self) -> str:
        """Generate a visual representation of the schedule."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"TA PAIRING SCHEDULE - {self.algorithm.value.upper()}")
        lines.append("=" * 70)
        lines.append(f"Participants: {self.real_participants} real" +
                    (f" + 1 ghost" if self.has_ghost else ""))
        lines.append(f"Total Rounds: {self.total_rounds}")
        lines.append(f"Matches per Round: {self.matches_per_round}")
        lines.append(f"Total Matches: {self.total_matches}")
        lines.append(f"Matches per Participant: {self.matches_per_participant}")
        lines.append("-" * 70)

        for round_num, round_matches in enumerate(self.rounds, 1):
            lines.append(f"\n📍 ROUND {round_num}")
            lines.append("-" * 40)
            for match in round_matches:
                ghost_marker = " 👻" if match.is_ghost_match else ""
                lines.append(
                    f"  Match {match.match_number}: "
                    f"[Seat {match.seat_a}] {match.participant_a.name} "
                    f"vs "
                    f"{match.participant_b.name} [Seat {match.seat_b}]"
                    f"{ghost_marker}"
                )

        lines.append("\n" + "=" * 70)
        lines.append("PARTICIPANT SCHEDULES")
        lines.append("=" * 70)

        for p_id, opponents in self.participant_schedule.items():
            if not opponents[0].startswith("GHOST"):  # Skip ghost's schedule
                opponent_list = ", ".join(opponents)
                lines.append(f"  {p_id}: plays against → {opponent_list}")

        return "\n".join(lines)

    def to_rotation_grid(self) -> str:
        """Generate a rotation grid showing seat positions per round."""
        lines = []
        lines.append("\n" + "=" * 70)
        lines.append("SEAT ROTATION GRID")
        lines.append("=" * 70)

        # Header
        header = "Participant".ljust(15)
        for r in range(1, self.total_rounds + 1):
            header += f"R{r}".center(6)
        lines.append(header)
        lines.append("-" * (15 + 6 * self.total_rounds))

        # Each participant's seats per round
        for p_id, schedule in self.rotation_map.items():
            row = str(p_id).ljust(15)
            for seat in schedule:
                row += str(seat).center(6)
            lines.append(row)

        return "\n".join(lines)

    def to_match_matrix(self) -> str:
        """Generate a matrix showing who plays whom and in which round."""
        lines = []
        lines.append("\n" + "=" * 70)
        lines.append("MATCH MATRIX (Round numbers)")
        lines.append("=" * 70)

        # Build matrix
        participants = sorted(self.participant_schedule.keys())
        n = len(participants)

        # Create lookup: (p1, p2) -> round_number
        match_lookup = {}
        for round_num, round_matches in enumerate(self.rounds, 1):
            for match in round_matches:
                key1 = (match.participant_a.name, match.participant_b.name)
                key2 = (match.participant_b.name, match.participant_a.name)
                match_lookup[key1] = round_num
                match_lookup[key2] = round_num

        # Header
        header = "".ljust(12)
        for p in participants:
            header += p[:8].center(10)
        lines.append(header)
        lines.append("-" * (12 + 10 * n))

        # Rows
        for p1 in participants:
            row = p1[:10].ljust(12)
            for p2 in participants:
                if p1 == p2:
                    row += "-".center(10)
                else:
                    round_num = match_lookup.get((p1, p2), "")
                    row += str(round_num).center(10)
            lines.append(row)

        return "\n".join(lines)


class TAPairingService:
    """
    Service for generating TA match pairings using various algorithms.
    """

    def __init__(self):
        self.participants: list[Participant] = []
        self.ghost: Optional[Participant] = None

    def generate_pairing(
        self,
        participants: list[dict],  # [{user_id, enrollment_id, name}, ...]
        algorithm: PairingAlgorithm = PairingAlgorithm.ROUND_ROBIN_FULL,
        custom_rounds: Optional[int] = None,
    ) -> PairingResult:
        """
        Generate match pairings using the specified algorithm.

        Args:
            participants: List of participant data dicts
            algorithm: Which pairing algorithm to use
            custom_rounds: Number of rounds (only for ROUND_ROBIN_CUSTOM)

        Returns:
            PairingResult with all matches and schedules
        """
        # Initialize participants
        self._initialize_participants(participants)

        n = len(self.participants)  # Includes ghost if added
        real_n = len(participants)

        # Calculate number of legs based on algorithm
        # FULL = N legs (extended, more matches - rotation continues/repeats)
        # HALF = N/2 legs (standard TA format)
        # CUSTOM = user-specified (up to N legs)

        if algorithm == PairingAlgorithm.ROUND_ROBIN_FULL:
            # Extended: N legs (double the standard, rotation continues)
            num_rounds = n
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_HALF:
            # Standard TA: N/2 legs
            num_rounds = n // 2
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_CUSTOM:
            if custom_rounds is None:
                raise ValueError("custom_rounds required for ROUND_ROBIN_CUSTOM")
            # Allow up to N legs for custom
            num_rounds = min(custom_rounds, n)
        elif algorithm == PairingAlgorithm.SIMPLE_PAIRS:
            num_rounds = 1
        else:
            raise ValueError(f"Unknown algorithm: {algorithm}")

        # Generate matches using circle method
        rounds = self._generate_round_robin(num_rounds)

        # Build participant schedules
        participant_schedule = self._build_participant_schedule(rounds)

        # Build rotation map
        rotation_map = self._build_rotation_map(rounds)

        # Calculate statistics
        matches_per_round = n // 2
        total_matches = sum(len(r) for r in rounds)
        matches_per_participant = num_rounds  # In round-robin, each plays once per round

        return PairingResult(
            algorithm=algorithm,
            total_participants=n,
            real_participants=real_n,
            has_ghost=self.ghost is not None,
            total_rounds=num_rounds,
            matches_per_round=matches_per_round,
            total_matches=total_matches,
            matches_per_participant=matches_per_participant,
            rounds=rounds,
            rotation_map=rotation_map,
            participant_schedule=participant_schedule,
        )

    def _initialize_participants(self, participant_data: list[dict]) -> None:
        """Initialize participants, adding ghost if odd number."""
        self.participants = []
        self.ghost = None

        for i, data in enumerate(participant_data, 1):
            p = Participant(
                id=i,
                user_id=data.get("user_id"),
                enrollment_id=data.get("enrollment_id"),
                name=data.get("name", f"Player {i}"),
                is_ghost=False,
            )
            self.participants.append(p)

        # Add ghost if odd number
        if len(self.participants) % 2 == 1:
            self.ghost = Participant(
                id=len(self.participants) + 1,
                user_id=None,
                enrollment_id=None,
                name="GHOST",
                is_ghost=True,
            )
            self.participants.append(self.ghost)

    def _generate_round_robin(self, num_rounds: int) -> list[list[Match]]:
        """
        Generate round-robin pairings using the TA rotation algorithm.

        TA Rotation Algorithm (from Django old code):
        1. Matches are ALWAYS between adjacent seats: (1,2), (3,4), (5,6), etc.
        2. Each leg, participants rotate seats using: odd seat → -2, even seat → +2
        3. Special leg at middle (total_legs/2 + 1) where even seats get +4 instead
        4. This ensures side-by-side matches while rotating opponents
        """
        n = len(self.participants)
        rounds = []

        # sector_draw[seat-1] = draw_number at that seat
        # Initially: seat 1 has draw 1, seat 2 has draw 2, etc.
        sector_draw = list(range(1, n + 1))

        # Determine special legs for cycle-breaking
        # For TA rotation with ±2, cycle length = N / gcd(4, N)
        # To ensure maximum unique pairings, apply +4 break at cycle boundaries
        total_legs = num_rounds
        cycle_length = n // math.gcd(4, n)

        # Apply special +4 rotation at the start of each new cycle
        special_legs = set()
        for k in range(1, (total_legs // cycle_length) + 2):
            special_leg = k * cycle_length + 1
            if special_leg <= total_legs:
                special_legs.add(special_leg)

        for leg in range(1, num_rounds + 1):
            round_matches = []
            match_num = 1

            # Create matches by pairing adjacent seats: (1,2), (3,4), etc.
            for seat_a in range(1, n + 1, 2):
                seat_b = seat_a + 1
                if seat_b > n:
                    break

                # Get draw numbers at these seats
                draw_a = sector_draw[seat_a - 1]
                draw_b = sector_draw[seat_b - 1]

                # Get participants by draw number (draw_number = participant id, 1-indexed)
                p_a = self.participants[draw_a - 1]
                p_b = self.participants[draw_b - 1]

                # Check for ghost match
                is_ghost = p_a.is_ghost or p_b.is_ghost
                ghost_side = None
                if p_a.is_ghost:
                    ghost_side = 'A'
                elif p_b.is_ghost:
                    ghost_side = 'B'

                match = Match(
                    round_number=leg,
                    match_number=match_num,
                    seat_a=seat_a,
                    seat_b=seat_b,
                    participant_a=p_a,
                    participant_b=p_b,
                    is_ghost_match=is_ghost,
                    ghost_side=ghost_side,
                )
                round_matches.append(match)
                match_num += 1

            rounds.append(round_matches)

            # Rotate seat assignments for next leg (unless last leg)
            if leg < num_rounds:
                next_leg = leg + 1
                new_sector_draw = sector_draw.copy()

                for seat in range(1, n + 1):
                    old_draw = sector_draw[seat - 1]

                    # Special legs: apply +4 to even seats at cycle boundaries
                    if (next_leg in special_legs) and (seat % 2 == 0):
                        new_draw = old_draw + 4
                        if new_draw > n:
                            new_draw -= n
                    else:
                        # Standard rotation: odd seat → -2, even seat → +2
                        if seat % 2 == 1:  # odd seat
                            new_draw = old_draw - 2
                            if new_draw < 1:
                                new_draw += n
                        else:  # even seat
                            new_draw = old_draw + 2
                            if new_draw > n:
                                new_draw -= n

                    new_sector_draw[seat - 1] = new_draw

                sector_draw = new_sector_draw

        return rounds

    def _build_participant_schedule(self, rounds: list[list[Match]]) -> dict:
        """Build schedule showing each participant's opponents."""
        schedule = {p.name: [] for p in self.participants}

        for round_matches in rounds:
            for match in round_matches:
                schedule[match.participant_a.name].append(match.participant_b.name)
                schedule[match.participant_b.name].append(match.participant_a.name)

        return schedule

    def _build_rotation_map(self, rounds: list[list[Match]]) -> dict:
        """Build map showing seat assignments per round."""
        rotation_map = {p.name: [] for p in self.participants}

        for round_matches in rounds:
            # Track seats this round
            round_seats = {}
            for match in round_matches:
                round_seats[match.participant_a.name] = match.seat_a
                round_seats[match.participant_b.name] = match.seat_b

            for p in self.participants:
                rotation_map[p.name].append(round_seats.get(p.name, "-"))

        return rotation_map

    @staticmethod
    def get_algorithm_info() -> dict:
        """Get information about available algorithms."""
        return {
            PairingAlgorithm.ROUND_ROBIN_FULL: {
                "name": "Extended TA (Full)",
                "description": "Extended TA format - N legs for MORE matches (rotation continues)",
                "formula": "N legs for N participants",
                "use_case": "Longer events, organizers want more matches per participant",
                "duration_example": "20 participants = 20 legs × 15min = ~5 hours",
            },
            PairingAlgorithm.ROUND_ROBIN_HALF: {
                "name": "Standard TA",
                "description": "Standard TA format - N/2 legs (normal duration)",
                "formula": "N/2 legs for N participants",
                "use_case": "Standard TA tournament format",
                "duration_example": "20 participants = 10 legs × 15min = ~2.5 hours",
            },
            PairingAlgorithm.ROUND_ROBIN_CUSTOM: {
                "name": "Custom TA",
                "description": "Specify exact number of legs (1 to N)",
                "formula": "User-defined legs",
                "use_case": "Flexible scheduling for any duration",
                "duration_example": "20 participants, 5 legs = 5 × 15min = ~1.25 hours",
            },
            PairingAlgorithm.SIMPLE_PAIRS: {
                "name": "Simple Pairs",
                "description": "Single leg, basic pairing",
                "formula": "1 leg only",
                "use_case": "Very quick events, single round",
                "duration_example": "20 participants = 1 leg × 15min = 15min",
            },
        }

    @staticmethod
    def calculate_event_duration(
        num_participants: int,
        algorithm: PairingAlgorithm,
        match_duration_minutes: int = 15,
        break_between_rounds_minutes: int = 5,
        custom_rounds: Optional[int] = None,
    ) -> dict:
        """Calculate estimated event duration."""
        # Adjust for odd number (add ghost)
        n = num_participants if num_participants % 2 == 0 else num_participants + 1

        if algorithm == PairingAlgorithm.ROUND_ROBIN_FULL:
            # Extended: N legs (more matches)
            num_legs = n
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_HALF:
            # Standard TA: N/2 legs
            num_legs = n // 2
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_CUSTOM:
            num_legs = min(custom_rounds or 1, n)
        else:
            num_legs = 1

        matches_per_leg = n // 2
        total_matches = num_legs * matches_per_leg

        # Each leg runs in parallel, so duration is per-leg, not per-match
        leg_duration = match_duration_minutes
        total_match_time = num_legs * leg_duration
        total_break_time = (num_legs - 1) * break_between_rounds_minutes if num_legs > 1 else 0
        total_duration = total_match_time + total_break_time

        return {
            "num_participants": num_participants,
            "effective_participants": n,
            "has_ghost": num_participants % 2 == 1,
            "algorithm": algorithm.value,
            "num_legs": num_legs,
            "matches_per_leg": matches_per_leg,
            "total_matches": total_matches,
            "match_duration_minutes": match_duration_minutes,
            "break_between_legs_minutes": break_between_rounds_minutes,
            "total_match_time_minutes": total_match_time,
            "total_break_time_minutes": total_break_time,
            "total_duration_minutes": total_duration,
            "total_duration_formatted": f"{total_duration // 60}h {total_duration % 60}min",
            "matches_per_participant": num_legs,
        }


def demo_pairing():
    """Demo the pairing service."""
    # Create sample participants
    participants = [
        {"user_id": 1, "enrollment_id": 101, "name": "Alex"},
        {"user_id": 2, "enrollment_id": 102, "name": "Bob"},
        {"user_id": 3, "enrollment_id": 103, "name": "Carol"},
        {"user_id": 4, "enrollment_id": 104, "name": "David"},
        {"user_id": 5, "enrollment_id": 105, "name": "Eva"},
        {"user_id": 6, "enrollment_id": 106, "name": "Frank"},
        {"user_id": 7, "enrollment_id": 107, "name": "Grace"},  # Odd - will add ghost
    ]

    service = TAPairingService()

    print("\n" + "=" * 80)
    print("DEMO: 7 Participants (will add 1 ghost for 8 total)")
    print("=" * 80)

    # Show algorithm info
    print("\nAvailable Algorithms:")
    for algo, info in service.get_algorithm_info().items():
        print(f"\n  {algo.value}:")
        print(f"    Name: {info['name']}")
        print(f"    Description: {info['description']}")
        print(f"    Formula: {info['formula']}")

    # Calculate durations for each algorithm
    print("\n" + "-" * 80)
    print("Duration Estimates (15min matches, 5min breaks):")
    print("-" * 80)

    for algo in PairingAlgorithm:
        custom = 3 if algo == PairingAlgorithm.ROUND_ROBIN_CUSTOM else None
        duration = service.calculate_event_duration(
            num_participants=7,
            algorithm=algo,
            match_duration_minutes=15,
            break_between_rounds_minutes=5,
            custom_rounds=custom,
        )
        print(f"\n  {algo.value}:")
        print(f"    Rounds: {duration['num_rounds']}")
        print(f"    Total Matches: {duration['total_matches']}")
        print(f"    Duration: {duration['total_duration_formatted']}")

    # Generate full round-robin
    print("\n" + "=" * 80)
    print("FULL ROUND ROBIN SCHEDULE")
    print("=" * 80)

    result = service.generate_pairing(
        participants=participants,
        algorithm=PairingAlgorithm.ROUND_ROBIN_FULL,
    )

    print(result.to_visual_schedule())
    print(result.to_rotation_grid())
    print(result.to_match_matrix())

    # Generate custom 3-round
    print("\n" + "=" * 80)
    print("CUSTOM 3-ROUND SCHEDULE")
    print("=" * 80)

    result_custom = service.generate_pairing(
        participants=participants,
        algorithm=PairingAlgorithm.ROUND_ROBIN_CUSTOM,
        custom_rounds=3,
    )

    print(result_custom.to_visual_schedule())


if __name__ == "__main__":
    demo_pairing()
