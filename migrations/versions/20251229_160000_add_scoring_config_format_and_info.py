"""Add format_code and calculation info to scoring_configs.

Adds:
- format_code: Which competition format this scoring config belongs to (sf, ta, tsf)
- calculation_info: Detailed explanation of how scoring works
- team_scoring_info: Explanation of team scoring for this config

Revision ID: 20251229_160000
Revises: 20251229_151700
Create Date: 2025-12-29 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251229_160000'
down_revision: Union[str, None] = '20251229_151700'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Scoring config explanations
SF_TOP_X_OVERALL = {
    'calculation_info': '''**Top X Catches Scoring**

How it works:
1. Each participant's catches are ranked by length (longest first)
2. Only the top X catches count toward the score (configurable, default 5)
3. Score = Sum of lengths of top X catches in centimeters
4. Tiebreaker: Total catch time (faster catches win)

Example (Top 5):
- Catch 1: 45cm, Catch 2: 38cm, Catch 3: 35cm, Catch 4: 32cm, Catch 5: 28cm
- Score = 45 + 38 + 35 + 32 + 28 = 178 points

Species diversity bonuses can be enabled to reward catching different species.''',

    'team_scoring_info': '''**Team Scoring (Top X Overall)**

Team score is calculated by summing individual scores of all team members.

Example (3-person team):
- Member A: 178 points (top 5 catches)
- Member B: 156 points (top 5 catches)
- Member C: 142 points (top 5 catches)
- Team Score = 178 + 156 + 142 = 476 points

Team leaderboard ranks teams by total combined score.'''
}

SF_TOP_X_BY_SPECIES = {
    'calculation_info': '''**Top X by Species Scoring (Slot-Based)**

How it works:
1. Each fish species has its own "slot" (configurable slots per species)
2. For each species, only the top X catches count
3. Score = Sum of all counting catches across all species
4. This rewards catching diverse species, not just one type

Example (3 slots per species):
- Pike: 52cm + 48cm + 45cm = 145 points
- Perch: 35cm + 32cm + 28cm = 95 points
- Zander: 65cm + 58cm = 123 points (only 2 caught)
- Total Score = 145 + 95 + 123 = 363 points

Minimum length rules can apply per species.''',

    'team_scoring_info': '''**Team Scoring (Top X by Species)**

Team score is the sum of all team members' individual scores.

Each member fills their own species slots independently.
Team total = Sum of all individual totals.

Alternative: Some events use "best catches per species across team" -
the team's best X catches of each species count regardless of who caught them.'''
}

TA_MATCH = {
    'calculation_info': '''**Head-to-Head Match Scoring (Trout Area)**

How it works:
1. Competitors face each other in 1v1 matches
2. Each match has multiple legs (fishing periods)
3. Per leg, the angler with more/bigger catches wins that leg
4. Match outcome determines points:
   - V (Victory): 3.0 points - Won match decisively
   - T (Tie+): 1.5 points - Tied but won on tiebreaker
   - T0 (Tie): 1.0 points - Perfect tie
   - L (Loss+): 0.5 points - Lost but close
   - L0 (Loss): 0.0 points - Lost decisively

Tournament phases:
1. Qualifier: Round-robin matches, top N advance
2. Requalification: Second chance for eliminated anglers
3. Semifinals & Finals: Knockout bracket''',

    'team_scoring_info': '''**Team Scoring (Trout Area)**

Teams compete in parallel matches. Team score = sum of member match points.

Example (3-person team, round 1):
- Member A vs Opponent A: Victory (3.0 pts)
- Member B vs Opponent B: Loss (0.0 pts)
- Member C vs Opponent C: Tie+ (1.5 pts)
- Team Round Score = 4.5 points

Team standings based on cumulative match points across all rounds.'''
}

TA_LENGTH = {
    'calculation_info': '''**Total Length Scoring (Trout Area)**

How it works:
1. All catches count toward the score
2. Score = Total length of all fish caught (in cm)
3. No slot limits - every fish adds to your total
4. Used in head-to-head matches to determine leg winners

Example match leg:
- Angler A: 5 fish, total 125cm
- Angler B: 4 fish, total 118cm
- Angler A wins the leg

This rewards both quantity and quality of catches.''',

    'team_scoring_info': '''**Team Scoring (TA Total Length)**

Team score = Sum of all team members' total lengths.

Can be used for:
- Team vs Team matches
- Aggregate team standings
- Mixed format with individual match points + team length bonus'''
}

TSF_GROUP = {
    'calculation_info': '''**Group Stage + Finals (Trout Shore Fishing)**

How it works:
1. Participants divided into sectors/groups
2. Multiple days, multiple legs per day
3. Each leg: Ranked by position within sector (1st, 2nd, 3rd...)
4. Position points: 1st=1pt, 2nd=2pt, 3rd=3pt (lower is better, golf-style)
5. Day score = Sum of position points across all legs
6. Final score = Sum of all day scores

Sector validators enter results (fast-paced, no self-validation).

Example Day (4 legs):
- Leg 1: 2nd place = 2 pts
- Leg 2: 1st place = 1 pt
- Leg 3: 3rd place = 3 pts
- Leg 4: 1st place = 1 pt
- Day Total = 7 points (lower is better!)''',

    'team_scoring_info': '''**Team Scoring (TSF Group Stage)**

Team score = Sum of all team members' position points.

Lower team total = Better ranking.

Example (2-person team, Day 1):
- Member A: 7 position points
- Member B: 9 position points
- Team Day Score = 16 points

Teams rotate through sectors to ensure fairness.'''
}

TSF_WEIGHT = {
    'calculation_info': '''**Total Catches Scoring (Trout Shore Fishing)**

How it works:
1. All catches count toward the score
2. Score can be based on:
   - Total weight of all fish
   - Total count of fish
   - Total length of all fish
3. Compared within sector for position ranking
4. Position points still determine final standings

This method values volume of catches while maintaining
the position-based ranking system of TSF.''',

    'team_scoring_info': '''**Team Scoring (TSF Total Catches)**

Team aggregate = Sum of all team members' catches.

Used for:
- Team weight/count leaderboard
- Determining team sector positions
- Tiebreakers in position-based scoring'''
}


def upgrade() -> None:
    # Add new columns
    op.add_column('scoring_configs', sa.Column('format_code', sa.String(10), nullable=False, server_default='sf'))
    op.add_column('scoring_configs', sa.Column('calculation_info', sa.Text(), nullable=True))
    op.add_column('scoring_configs', sa.Column('team_scoring_info', sa.Text(), nullable=True))

    # Update format_code based on scoring config code prefix
    op.execute("""
        UPDATE scoring_configs SET format_code = 'sf' WHERE code LIKE 'sf_%';
        UPDATE scoring_configs SET format_code = 'ta' WHERE code LIKE 'ta_%';
        UPDATE scoring_configs SET format_code = 'tsf' WHERE code LIKE 'tsf_%';
    """)

    # Update calculation_info and team_scoring_info for each config
    # SF Top X Overall
    op.execute(f"""
        UPDATE scoring_configs
        SET calculation_info = $${SF_TOP_X_OVERALL['calculation_info']}$$,
            team_scoring_info = $${SF_TOP_X_OVERALL['team_scoring_info']}$$
        WHERE code = 'sf_top_x_overall';
    """)

    # SF Top X by Species
    op.execute(f"""
        UPDATE scoring_configs
        SET calculation_info = $${SF_TOP_X_BY_SPECIES['calculation_info']}$$,
            team_scoring_info = $${SF_TOP_X_BY_SPECIES['team_scoring_info']}$$
        WHERE code = 'sf_top_x_by_species';
    """)

    # TA Match
    op.execute(f"""
        UPDATE scoring_configs
        SET calculation_info = $${TA_MATCH['calculation_info']}$$,
            team_scoring_info = $${TA_MATCH['team_scoring_info']}$$
        WHERE code = 'ta_match';
    """)

    # TA Length
    op.execute(f"""
        UPDATE scoring_configs
        SET calculation_info = $${TA_LENGTH['calculation_info']}$$,
            team_scoring_info = $${TA_LENGTH['team_scoring_info']}$$
        WHERE code = 'ta_length';
    """)

    # TSF Group
    op.execute(f"""
        UPDATE scoring_configs
        SET calculation_info = $${TSF_GROUP['calculation_info']}$$,
            team_scoring_info = $${TSF_GROUP['team_scoring_info']}$$
        WHERE code = 'tsf_group';
    """)

    # TSF Weight
    op.execute(f"""
        UPDATE scoring_configs
        SET calculation_info = $${TSF_WEIGHT['calculation_info']}$$,
            team_scoring_info = $${TSF_WEIGHT['team_scoring_info']}$$
        WHERE code = 'tsf_weight';
    """)


def downgrade() -> None:
    op.drop_column('scoring_configs', 'team_scoring_info')
    op.drop_column('scoring_configs', 'calculation_info')
    op.drop_column('scoring_configs', 'format_code')
