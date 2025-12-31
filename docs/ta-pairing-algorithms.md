# Trout Area (TA) Pairing Algorithms Documentation

## Overview

This document describes the pairing algorithms used for Trout Area (TA) fishing competitions in the Reelin platform. TA events use a rotation-based system where participants compete in side-by-side matches at adjacent seats.

## Key Concepts

### Seat Layout
- Participants sit at numbered seats: 1, 2, 3, 4, 5, 6, etc.
- Matches are ALWAYS between **adjacent seats**: (1-2), (3-4), (5-6), etc.
- This ensures side-by-side competition at the fishing water

### Draw Number
- Each participant is assigned a random `draw_number` at enrollment
- The draw number determines their initial seat position
- Draw numbers rotate through seats across legs using the rotation algorithm

### Ghost Participant
- If odd number of participants, a "ghost" is added to make it even
- Matches against ghost = automatic win (BYE)
- Ghost is included in rotation calculations

## Rotation Algorithm (Django Production Code)

The rotation algorithm comes from the original Django production code and ensures fair distribution of opponents while maintaining adjacent seat matches.

### Basic Rotation Rules
```
For each leg transition:
- Odd seats (1, 3, 5, ...):  draw_number = old_draw - 2  (wrap around if < 1)
- Even seats (2, 4, 6, ...): draw_number = old_draw + 2  (wrap around if > N)
```

### Special Leg Rule
When `total_legs` is even, there's a special leg at the middle (`total_legs/2 + 1`) where:
```
Even seats get +4 instead of +2 (to break the cycle and ensure more unique pairings)
```

### Python Implementation
```python
def rotate_seats(sector_draw, n, leg, total_legs):
    """
    Rotate seat assignments for the next leg.

    Args:
        sector_draw: List where sector_draw[seat-1] = draw_number at that seat
        n: Total participants (including ghost)
        leg: Current leg number
        total_legs: Total number of legs
    """
    next_leg = leg + 1
    new_sector_draw = sector_draw.copy()

    # Determine special leg (one-time +4 for even seats)
    special_leg = None
    if total_legs % 2 == 0:
        special_leg = (total_legs // 2) + 1

    for seat in range(1, n + 1):
        old_draw = sector_draw[seat - 1]

        # Special leg: apply +4 to even seats
        if special_leg and (next_leg == special_leg) and (seat % 2 == 0):
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

    return new_sector_draw
```

## Available Algorithms

### 1. ROUND_ROBIN_FULL (Extended)
- **Legs**: N (where N = total participants including ghost)
- **Use case**: Organizers want MORE matches per participant
- **Duration**: Longest format
- **Pairings**: Rotation continues past first cycle, some pairings repeat

```
Example: 19 users (20 with ghost)
- Legs: 20
- Matches per leg: 10
- Total matches: 200
- Matches per participant: 20
- Estimated duration: ~6.5 hours (15min/leg + 5min breaks)
```

### 2. ROUND_ROBIN_HALF (Standard TA)
- **Legs**: N/2 (standard TA format)
- **Use case**: Normal tournament duration
- **Duration**: Standard format
- **Pairings**: All unique pairings (no repeats)

```
Example: 19 users (20 with ghost)
- Legs: 10
- Matches per leg: 10
- Total matches: 100
- Matches per participant: 10
- Estimated duration: ~3.25 hours
```

### 3. ROUND_ROBIN_CUSTOM
- **Legs**: User-specified (1 to N)
- **Use case**: Flexible scheduling for any duration
- **Safety**: ALL users always get same number of matches (1 per leg)

```
Example: 19 users, custom=5 legs
- Legs: 5
- Matches per leg: 10
- Total matches: 50
- Matches per participant: 5
- Estimated duration: ~1.6 hours
```

### 4. SIMPLE_PAIRS
- **Legs**: 1 only
- **Use case**: Very quick events, elimination rounds
- **Pairings**: Single round, adjacent seat matches only

```
Example: 8 users
- Legs: 1
- Matches: 4
- Each user plays exactly 1 match

Leg 1:
  Seat 1-2: User1 vs User2
  Seat 3-4: User3 vs User4
  Seat 5-6: User5 vs User6
  Seat 7-8: User7 vs User8
```

## Algorithm Comparison Table

| Algorithm | Formula | Legs (N=20) | Matches | Duration* |
|-----------|---------|-------------|---------|-----------|
| FULL | N | 20 | 200 | ~6.5h |
| HALF | N/2 | 10 | 100 | ~3.25h |
| CUSTOM(5) | user-defined | 5 | 50 | ~1.6h |
| SIMPLE_PAIRS | 1 | 1 | 10 | ~15min |

*Duration assumes 15min per leg + 5min breaks

## Test Data Examples

### Example 1: 4 Participants (HALF - Standard)

```
Participants: User1, User2, User3, User4
Algorithm: ROUND_ROBIN_HALF
Legs: 2 (N/2 = 4/2 = 2)

Initial draw assignment (random):
  Draw 1 → User1
  Draw 2 → User2
  Draw 3 → User3
  Draw 4 → User4

Leg 1 (initial seats = draw numbers):
  Seat 1-2: User1 vs User2
  Seat 3-4: User3 vs User4

Rotation for Leg 2:
  Seat 1: draw 1-2 = -1+4 = 3 → User3
  Seat 2: draw 2+2 = 4 → User4
  Seat 3: draw 3-2 = 1 → User1
  Seat 4: draw 4+2 = 6-4 = 2 → User2

Leg 2:
  Seat 1-2: User3 vs User4
  Seat 3-4: User1 vs User2

Result: All 4 unique pairings achieved in 2 legs
```

### Example 2: 6 Participants (HALF - Standard)

```
Participants: User1-User6
Algorithm: ROUND_ROBIN_HALF
Legs: 3 (N/2 = 6/2 = 3)

Leg 1:
  Seat 1-2: User1 vs User2
  Seat 3-4: User3 vs User4
  Seat 5-6: User5 vs User6

Leg 2:
  Seat 1-2: User5 vs User4
  Seat 3-4: User1 vs User6
  Seat 5-6: User3 vs User2

Leg 3:
  Seat 1-2: User3 vs User6
  Seat 3-4: User5 vs User2
  Seat 5-6: User1 vs User4

Opponent Schedule:
  User1: vs User2, User6, User4
  User2: vs User1, User3, User5
  User3: vs User4, User2, User6
  User4: vs User3, User5, User1
  User5: vs User6, User4, User2
  User6: vs User5, User1, User3

All 9 unique pairings achieved, each user plays 3 different opponents
```

### Example 3: 8 Participants (FULL - Extended)

```
Participants: User1-User8
Algorithm: ROUND_ROBIN_FULL
Legs: 8 (N = 8)

Legs 1-4: All 16 unique pairings
Legs 5-8: Rotation continues, pairings repeat but at different seats

Total: 32 matches, each user plays 8 matches
```

## Production Data Reference

### Events 502 and 503 (Cupa de Noapte 2025)

The organizer wanted more matches than standard N/2 legs allowed, so they created 2 separate events with the same 19 users.

```sql
-- Event 502: 2025 Cupa de Noapte - Mansa 1
-- Event 503: 2025 Cupa de Noapte - Mansa 2

SELECT event_id,
       COUNT(*) as lineup_rows,
       COUNT(DISTINCT user_id) as unique_users,
       COUNT(DISTINCT leg) as legs
FROM competitions_initiallineup
WHERE event_id IN (502, 503)
GROUP BY event_id;

-- Results:
-- event_id | lineup_rows | unique_users | legs
-- 502      | 200         | 19           | 10
-- 503      | 200         | 19           | 10

SELECT event_id,
       COUNT(*) as match_rows,
       COUNT(DISTINCT leg_number) as legs
FROM competitions_eventtamatch
WHERE event_id IN (502, 503)
GROUP BY event_id;

-- Results:
-- event_id | match_rows | legs
-- 502      | 100        | 10
-- 503      | 100        | 10
```

**Analysis:**
- 19 users + 1 ghost = 20 participants
- Each event: N/2 = 10 legs (ROUND_ROBIN_HALF equivalent)
- Combined: 20 legs, 200 matches

**With ROUND_ROBIN_FULL:**
- Single event with 20 legs would give same 200 matches
- No need to create 2 separate events

## Code Location

The pairing service is implemented in:
```
/app/services/ta_pairing.py
```

### Key Classes and Methods

```python
class PairingAlgorithm(str, Enum):
    ROUND_ROBIN_FULL = "round_robin_full"   # N legs
    ROUND_ROBIN_HALF = "round_robin_half"   # N/2 legs (standard)
    ROUND_ROBIN_CUSTOM = "round_robin_custom"
    SIMPLE_PAIRS = "simple_pairs"           # 1 leg

class TAPairingService:
    def generate_pairing(
        self,
        participants: list[dict],
        algorithm: PairingAlgorithm,
        custom_rounds: Optional[int] = None,
    ) -> PairingResult

    def _generate_round_robin(self, num_rounds: int) -> list[list[Match]]

    @staticmethod
    def calculate_event_duration(
        num_participants: int,
        algorithm: PairingAlgorithm,
        match_duration_minutes: int = 15,
        break_between_rounds_minutes: int = 5,
        custom_rounds: Optional[int] = None,
    ) -> dict
```

### Usage Example

```python
from app.services.ta_pairing import TAPairingService, PairingAlgorithm

# Create participants list
participants = [
    {"user_id": 1, "enrollment_id": 101, "name": "John"},
    {"user_id": 2, "enrollment_id": 102, "name": "Jane"},
    {"user_id": 3, "enrollment_id": 103, "name": "Bob"},
    {"user_id": 4, "enrollment_id": 104, "name": "Alice"},
]

# Generate pairing
service = TAPairingService()
result = service.generate_pairing(
    participants=participants,
    algorithm=PairingAlgorithm.ROUND_ROBIN_HALF,  # Standard TA
)

# Access results
print(f"Total legs: {result.total_rounds}")
print(f"Total matches: {result.total_matches}")

for leg_idx, leg_matches in enumerate(result.rounds, 1):
    print(f"\nLeg {leg_idx}:")
    for match in leg_matches:
        print(f"  Seat {match.seat_a}-{match.seat_b}: "
              f"{match.participant_a.name} vs {match.participant_b.name}")

# Calculate duration
duration = TAPairingService.calculate_event_duration(
    num_participants=4,
    algorithm=PairingAlgorithm.ROUND_ROBIN_HALF,
    match_duration_minutes=15,
)
print(f"\nEstimated duration: {duration['total_duration_formatted']}")
```

## API Endpoints

### Generate Lineups
```
POST /api/v1/ta/events/{event_id}/lineups/generate
Body: {"algorithm": "round_robin_half"}  # or round_robin_full, round_robin_custom, simple_pairs
      {"algorithm": "round_robin_custom", "custom_legs": 5}  # for custom
```

### Get Schedule
```
GET /api/v1/ta/events/{event_id}/schedule
```

### Get Game Cards (User's matches)
```
GET /api/v1/ta/events/{event_id}/game-cards/my
```

## Important Notes

1. **All users always get the same number of matches** - The rotation algorithm ensures fair distribution

2. **Adjacent seat matching is mandatory** - TA competitions require side-by-side matches at the water

3. **Ghost handling** - Odd participant count automatically adds a ghost; matches vs ghost are automatic BYEs

4. **CUSTOM is safe** - Cannot result in some users missing matches; if custom_legs > N, it caps at N

5. **FULL for more matches** - Use ROUND_ROBIN_FULL instead of creating multiple events with same users

## Revision History

| Date | Change |
|------|--------|
| 2025-12-31 | Initial documentation created |
| 2025-12-31 | Added production data reference (events 502, 503) |
| 2025-12-31 | Updated algorithm formulas: FULL=N, HALF=N/2 |
