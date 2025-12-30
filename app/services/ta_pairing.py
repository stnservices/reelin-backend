"""
Trout Area (TA) Pairing & Lineup Generation Service.

Supports multiple pairing algorithms for TA competitions:

1. ROUND_ROBIN_FULL - Everyone plays everyone once (N-1 rounds for N participants)
2. ROUND_ROBIN_HALF - Everyone plays half the field (N/2 rounds)
3. ROUND_ROBIN_CUSTOM - Specify exact number of rounds (1 to N-1)
4. SIMPLE_PAIRS - Basic n/2 single-leg pairing (fastest, for quick events)

Features:
- Ghost participant handling for odd numbers
- No duplicate matches within the specified rounds
- Fair distribution (everyone plays same number of matches)
- Visual schedule/map generation
- Seat rotation patterns between legs

Classic Round-Robin (Circle Method):
- Fix participant 1 in position
- Rotate others clockwise each round
- Creates all unique pairings without duplicates
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math
import json


class PairingAlgorithm(str, Enum):
    """Available pairing algorithms for TA events."""
    ROUND_ROBIN_FULL = "round_robin_full"      # N-1 rounds, everyone plays everyone
    ROUND_ROBIN_HALF = "round_robin_half"      # N/2 rounds, everyone plays half
    ROUND_ROBIN_CUSTOM = "round_robin_custom"  # User-specified number of rounds
    SIMPLE_PAIRS = "simple_pairs"              # Basic n/2 pairing, single leg


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

        # Calculate number of rounds based on algorithm
        if algorithm == PairingAlgorithm.ROUND_ROBIN_FULL:
            num_rounds = n - 1
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_HALF:
            num_rounds = n // 2
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_CUSTOM:
            if custom_rounds is None:
                raise ValueError("custom_rounds required for ROUND_ROBIN_CUSTOM")
            max_rounds = n - 1
            num_rounds = min(custom_rounds, max_rounds)
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
        Generate round-robin pairings using the Circle Method.

        Circle Method:
        1. Arrange participants in two rows
        2. Fix position 0, rotate others clockwise each round
        3. Pair top row with bottom row

        Example with 6 participants:
        Round 1: [1,2,3] vs [6,5,4] → (1,6), (2,5), (3,4)
        Round 2: [1,6,2] vs [3,4,5] → (1,3), (6,4), (2,5) - rotated
        ...
        """
        n = len(self.participants)
        rounds = []

        # Create rotation list (exclude position 0 which stays fixed)
        rotation = list(range(n))

        for round_num in range(1, num_rounds + 1):
            round_matches = []
            match_num = 1

            # Split into two halves
            half = n // 2
            top_row = rotation[:half]
            bottom_row = rotation[half:][::-1]  # Reverse bottom row

            # Create matches by pairing top with bottom
            for i in range(half):
                p_a = self.participants[top_row[i]]
                p_b = self.participants[bottom_row[i]]

                # Check for ghost match
                is_ghost = p_a.is_ghost or p_b.is_ghost
                ghost_side = None
                if p_a.is_ghost:
                    ghost_side = 'A'
                elif p_b.is_ghost:
                    ghost_side = 'B'

                match = Match(
                    round_number=round_num,
                    match_number=match_num,
                    seat_a=top_row[i] + 1,  # 1-based seats
                    seat_b=bottom_row[i] + 1,
                    participant_a=p_a,
                    participant_b=p_b,
                    is_ghost_match=is_ghost,
                    ghost_side=ghost_side,
                )
                round_matches.append(match)
                match_num += 1

            rounds.append(round_matches)

            # Rotate for next round (keep position 0 fixed, rotate rest)
            rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]

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
                "name": "Full Round Robin",
                "description": "Everyone plays everyone exactly once",
                "formula": "N-1 rounds for N participants",
                "use_case": "Full tournament, fair complete coverage",
                "duration_example": "20 participants = 19 rounds × 15min = ~5 hours",
            },
            PairingAlgorithm.ROUND_ROBIN_HALF: {
                "name": "Half Round Robin",
                "description": "Everyone plays half the field",
                "formula": "N/2 rounds for N participants",
                "use_case": "Shorter events, still fair distribution",
                "duration_example": "20 participants = 10 rounds × 15min = ~2.5 hours",
            },
            PairingAlgorithm.ROUND_ROBIN_CUSTOM: {
                "name": "Custom Round Robin",
                "description": "Specify exact number of rounds (1 to N-1)",
                "formula": "User-defined rounds",
                "use_case": "Time-constrained events, flexible scheduling",
                "duration_example": "20 participants, 5 rounds = 5 × 15min = ~1.25 hours",
            },
            PairingAlgorithm.SIMPLE_PAIRS: {
                "name": "Simple Pairs",
                "description": "Single round, basic n/2 pairing",
                "formula": "1 round only",
                "use_case": "Very quick events, single elimination style",
                "duration_example": "20 participants = 1 round × 15min = 15min",
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
        # Adjust for odd number
        n = num_participants if num_participants % 2 == 0 else num_participants + 1

        if algorithm == PairingAlgorithm.ROUND_ROBIN_FULL:
            num_rounds = n - 1
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_HALF:
            num_rounds = n // 2
        elif algorithm == PairingAlgorithm.ROUND_ROBIN_CUSTOM:
            num_rounds = min(custom_rounds or 1, n - 1)
        else:
            num_rounds = 1

        matches_per_round = n // 2
        total_matches = num_rounds * matches_per_round

        # Each round runs in parallel, so duration is per-round, not per-match
        round_duration = match_duration_minutes
        total_match_time = num_rounds * round_duration
        total_break_time = (num_rounds - 1) * break_between_rounds_minutes if num_rounds > 1 else 0
        total_duration = total_match_time + total_break_time

        return {
            "num_participants": num_participants,
            "effective_participants": n,
            "has_ghost": num_participants % 2 == 1,
            "algorithm": algorithm.value,
            "num_rounds": num_rounds,
            "matches_per_round": matches_per_round,
            "total_matches": total_matches,
            "match_duration_minutes": match_duration_minutes,
            "break_between_rounds_minutes": break_between_rounds_minutes,
            "total_match_time_minutes": total_match_time,
            "total_break_time_minutes": total_break_time,
            "total_duration_minutes": total_duration,
            "total_duration_formatted": f"{total_duration // 60}h {total_duration % 60}min",
            "matches_per_participant": num_rounds,
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
