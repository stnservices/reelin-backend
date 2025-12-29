# ReelIn v2 Backend Documentation

## Overview

ReelIn is a fishing tournament management platform built with FastAPI, SQLAlchemy (async), and PostgreSQL.

## Architecture

```
reelin-backend/
├── app/
│   ├── main.py              # FastAPI application entry point
│   ├── config.py            # Pydantic settings
│   ├── database.py          # SQLAlchemy async setup
│   ├── dependencies.py      # Shared FastAPI dependencies
│   │
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── user.py          # UserAccount, UserProfile, TokenBlacklist
│   │   ├── event.py         # Event, EventType, ScoringConfig, EventPrize, EventScoringRule
│   │   ├── enrollment.py    # EventEnrollment
│   │   ├── catch.py         # Catch, EventScoreboard, RankingMovement
│   │   ├── club.py          # Club, ClubMembership
│   │   ├── fish.py          # Fish species
│   │   ├── location.py      # Country, City, FishingSpot
│   │   ├── notification.py  # Notification
│   │   └── sponsor.py       # Sponsor
│   │
│   ├── schemas/             # Pydantic request/response schemas
│   ├── api/                 # API routes
│   │   ├── v1/              # Public API v1
│   │   └── admin/           # Admin-only endpoints
│   │
│   ├── core/                # Core utilities
│   │   ├── security.py      # JWT, password hashing
│   │   └── permissions.py   # Role-based access
│   │
│   └── services/            # Business logic layer
│
├── migrations/              # Alembic migrations
└── docker/                  # Docker configuration
```

---

## Data Models

### User System

#### UserAccount
Primary authentication entity.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| email | String(255) | Unique, lowercase |
| password_hash | String(255) | Bcrypt hashed |
| is_active | Boolean | Account active status |
| is_staff | Boolean | Staff access flag |
| is_superuser | Boolean | Superuser flag |
| is_verified | Boolean | Email verified |
| created_at | DateTime | Account creation |
| last_login | DateTime | Last login timestamp |

#### UserProfile
Extended user information.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| user_id | Integer | FK to UserAccount |
| first_name | String(100) | First name |
| last_name | String(100) | Last name |
| phone | String(20) | Phone number |
| bio | Text | User biography |
| profile_picture_url | String(500) | Profile image URL |
| roles | JSONB | Array of role strings |
| country_id | Integer | FK to Country |
| city_id | Integer | FK to City |

**Available Roles:**
- `angler` - Basic user (can enroll in events)
- `organizer` - Can create and manage events
- `validator` - Can validate catches
- `administrator` - Full system access
- `sponsor` - Sponsor representative

---

### Event System

#### EventType
Configurable event types (admin-managed).

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| name | String(100) | Display name (e.g., "Street Fishing") |
| code | String(50) | Unique code (e.g., "street_fishing") |
| description | Text | Type description |
| icon_url | String(500) | Icon image URL |
| is_active | Boolean | Available for new events |

**Default Event Types:**
- Street Fishing
- Trout Area
- Trout Shore Fishing

#### ScoringConfig
Scoring rules tied to event types.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| event_type_id | Integer | FK to EventType |
| name | String(100) | Display name |
| code | String(50) | Unique code |
| description | Text | Configuration description |
| rules | JSONB | Flexible scoring rules |
| is_active | Boolean | Available for use |

**Scoring Types:**
1. **Type_1 (All Catches)** - All valid catches count up to slot limit per species
2. **Top_X_Catches** - Only best X catches count regardless of species
3. **Points_by_Length** - Points = fish length in cm

**Rules JSONB Structure:**
```json
{
  "top_count": 5,
  "scoring_method": "length",
  "tie_breaker": ["total_catches", "species_count", "biggest_catch", "first_catch_time"]
}
```

#### Event
Main competition entity.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| name | String(200) | Event name |
| slug | String(250) | URL-friendly identifier |
| description | Text | Event description |
| event_type_id | Integer | FK to EventType |
| scoring_config_id | Integer | FK to ScoringConfig |
| start_date | DateTime | Scheduled start |
| end_date | DateTime | Scheduled end |
| registration_deadline | DateTime | Enrollment cutoff |
| location_id | Integer | FK to FishingSpot (optional) |
| location_name | String(200) | Custom location text |
| created_by_id | Integer | FK to UserAccount |
| status | String(20) | Event state |
| max_participants | Integer | Capacity limit |
| requires_approval | Boolean | Manual enrollment approval |
| rules | Text | Event rules text |
| image_url | String(500) | Event banner image |

**Event Status Flow:**
```
draft → published → ongoing → completed
                  ↓
               cancelled
```

| Status | Description |
|--------|-------------|
| draft | Initial state, only creator can see |
| published | Visible to all, enrollment open |
| ongoing | Event in progress, catches accepted |
| completed | Event finished, rankings finalized |
| cancelled | Event cancelled |

#### EventFishScoring
Per-event, per-fish scoring configuration.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| event_id | Integer | FK to Event |
| fish_id | Integer | FK to Fish |
| accountable_catch_slots | Integer | How many catches count for this fish |
| accountable_min_length | Decimal | Minimum length (cm) to be valid |
| under_min_length_points | Integer | Points for undersized catches |
| top_x_catches | Integer | For Top_X scoring type |

**Business Rules:**
- Each event can have multiple fish species
- Each species has its own min length and slot count
- Catches below min_length get penalty points (under_min_length_points)
- Only top N catches per species count (accountable_catch_slots)

#### EventSpeciesBonusPoints
Species diversity bonus system.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| event_id | Integer | FK to Event |
| species_count | Integer | Number of distinct species threshold |
| bonus_points | Integer | Points awarded at this threshold |

**Example Configuration:**
```
species_count=2, bonus_points=10   → Catch 2 species = +10 points
species_count=3, bonus_points=25   → Catch 3 species = +25 points
species_count=4, bonus_points=50   → Catch 4 species = +50 points
```

**Rules:**
- User gets HIGHEST bonus they qualify for (not cumulative)
- Applied after all catch points are calculated
- Minimum 2 species required for any bonus

---

### Enrollment System

#### EventEnrollment

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| event_id | Integer | FK to Event |
| user_id | Integer | FK to UserAccount |
| status | String(20) | Enrollment state |
| enrollment_number | Integer | Sequential per event |
| draw_number | Integer | Random draw assignment |
| enrolled_at | DateTime | Enrollment timestamp |
| approved_by_id | Integer | FK to UserAccount |
| approved_at | DateTime | Approval timestamp |

**Enrollment Status Flow:**
```
pending → approved
        ↓
      rejected (record deleted)
```

**Business Rules:**
1. User can only enroll once per event
2. User must have phone number on profile
3. User cannot enroll if already approved for overlapping event
4. enrollment_number auto-increments per event
5. draw_number assigned randomly when all enrollments approved

---

### Catch & Scoring System

#### Catch
Photo captures with fish data.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| event_id | Integer | FK to Event |
| user_id | Integer | FK to UserAccount |
| fish_id | Integer | FK to Fish |
| length | Decimal | Fish length in cm |
| weight | Decimal | Fish weight (optional) |
| photo_url | String(500) | Photo storage URL |
| photo_hash | String(64) | MD5 hash for duplicate detection |
| location_lat | Decimal | GPS latitude |
| location_lng | Decimal | GPS longitude |
| submitted_at | DateTime | Upload timestamp |
| status | String(20) | Validation state |
| validated_by_id | Integer | FK to UserAccount |
| validated_at | DateTime | Validation timestamp |
| rejection_reason | Text | Reason if rejected |
| points | Integer | Calculated points |
| fish_rank | Integer | Rank among user's catches of this species |

**Catch Status Flow:**
```
pending → approved → (in leaderboard)
        ↓
      rejected → (removed from leaderboard)
```

#### EventScoreboard
Final rankings per event.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| event_id | Integer | FK to Event |
| user_id | Integer | FK to UserAccount |
| rank | Integer | Final position |
| total_catches | Integer | Number of valid catches |
| total_species | Integer | Number of distinct species |
| total_points | Integer | Sum of catch points + bonus |
| bonus_points | Integer | Species diversity bonus |
| penalty_points | Integer | Deductions from violations |
| biggest_catch | Decimal | Largest fish length |
| average_catch | Decimal | Average fish length |
| first_catch_at | DateTime | Time of first catch |
| details | JSONB | Detailed catch breakdown |

**Ranking Tiebreaker Order:**
1. Total points (higher wins)
2. Total valid catches (more wins)
3. Total species (more wins)
4. Biggest catch (larger wins)
5. Average catch (higher wins)
6. First catch time (earlier wins)

---

### Supporting Models

#### Fish
Fish species reference data.

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| name | String(100) | Common name |
| scientific_name | String(150) | Latin name |
| min_length | Decimal | Default minimum length |
| max_length | Decimal | Maximum typical length |
| image_url | String(500) | Species image |
| is_active | Boolean | Available for events |

#### Location Models

**Country:**
| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| name | String(100) | Country name |
| code | String(3) | ISO code |

**City:**
| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| country_id | Integer | FK to Country |
| name | String(100) | City name |

**FishingSpot:**
| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary key |
| city_id | Integer | FK to City |
| name | String(200) | Spot name |
| description | Text | Details |
| latitude | Decimal | GPS latitude |
| longitude | Decimal | GPS longitude |

---

## API Endpoints

### Authentication (`/api/v1/auth`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | /register | Create new user | No |
| POST | /login | Get JWT tokens | No |
| POST | /refresh | Refresh access token | No |
| POST | /logout | Invalidate token | Yes |
| GET | /me | Get current user | Yes |

### Users (`/api/v1/users`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | / | List users (admin) | Admin |
| GET | /profile | Get own profile | Yes |
| PATCH | /profile | Update own profile | Yes |
| GET | /{user_id} | Get user by ID | Yes |

### Events (`/api/v1/events`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | / | List events | Optional |
| GET | /types | List event types | No |
| GET | /scoring-configs | List scoring configs | No |
| GET | /{id} | Get event details | Optional |
| POST | / | Create event | Organizer |
| PATCH | /{id} | Update event | Organizer |
| POST | /{id}/publish | Publish event | Organizer |
| POST | /{id}/start | Start event | Organizer |
| POST | /{id}/stop | End event | Organizer |
| GET | /{id}/fish-scoring | List fish scoring configs | Yes |
| POST | /{id}/fish-scoring | Add fish species | Organizer |
| PATCH | /{id}/fish-scoring/{scoringId} | Update fish scoring | Organizer |
| DELETE | /{id}/fish-scoring/{scoringId} | Remove fish species | Organizer |
| GET | /{id}/bonus-points | List bonus points configs | Yes |
| POST | /{id}/bonus-points | Add bonus points | Organizer |
| DELETE | /{id}/bonus-points/{bonusId} | Remove bonus points | Organizer |

### Enrollments (`/api/v1/enrollments`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | / | List enrollments | Yes |
| POST | / | Create enrollment | Yes |
| GET | /{id} | Get enrollment | Yes |
| PATCH | /{id} | Update enrollment status | Organizer |
| DELETE | /{id} | Cancel enrollment | Yes |
| POST | /{id}/assign-number | Assign draw number | Organizer |

### Catches (`/api/v1/catches`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | / | List catches | Yes |
| POST | / | Submit catch | Yes |
| GET | /{id} | Get catch details | Yes |
| PATCH | /{id}/validate | Approve/reject catch | Validator |

### Admin Settings (`/api/admin/settings`)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET/POST/PATCH | /event-types | Manage event types | Admin |
| GET/POST/PATCH | /scoring-configs | Manage scoring configs | Admin |
| GET/POST/PATCH | /fish | Manage fish species | Admin |
| GET/POST/PATCH | /sponsors | Manage sponsors | Admin |
| GET/POST | /countries | Manage countries | Admin |
| GET/POST | /cities | Manage cities | Admin |
| GET/POST/PATCH | /fishing-spots | Manage fishing spots | Admin |

---

## Authentication & Authorization

### JWT Token Structure

**Access Token (15 min):**
```json
{
  "sub": "user_id",
  "type": "access",
  "jti": "unique_id",
  "exp": "expiration_time"
}
```

**Refresh Token (7 days):**
```json
{
  "sub": "user_id",
  "type": "refresh",
  "jti": "unique_id",
  "exp": "expiration_time"
}
```

### Role-Based Permissions

```python
from app.core.permissions import AdminOnly, OrganizerOrAdmin, ValidatorOrAdmin

# Admin only endpoint
@router.get("/admin-only")
async def admin_endpoint(current_user: UserAccount = Depends(AdminOnly)):
    ...

# Organizer or Admin
@router.post("/events")
async def create_event(current_user: UserAccount = Depends(OrganizerOrAdmin)):
    ...
```

---

## Scoring Calculation Logic

### Points Calculation

```python
def calculate_points(fish_length: Decimal, event_fish_scoring: EventFishScoring) -> int:
    if fish_length >= event_fish_scoring.accountable_min_length:
        return int(fish_length)  # 1 point per cm
    else:
        return event_fish_scoring.under_min_length_points  # Penalty points
```

### Fish Rank Calculation

For each user, catches are ranked within each species:
1. Sort by points DESC
2. Sort by length DESC (tiebreaker)
3. Sort by submitted_at ASC (tiebreaker)

Only catches with `fish_rank <= accountable_catch_slots` count toward total.

### Final Score Calculation

```python
total_points = sum(catch.points for catch in valid_catches)

# Add bonus points
species_count = len(set(catch.fish_id for catch in valid_catches))
bonus = get_highest_bonus(event, species_count)
total_points += bonus

# Subtract penalties
total_points -= penalty_points

# Rank by tiebreaker order
```

---

## Environment Variables

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/reelin

# Security
SECRET_KEY=your-secret-key-here
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# CORS
CORS_ORIGINS=http://localhost:3000

# Debug
DEBUG=true
```

---

## Running the Backend

### Development

```bash
# With Docker
docker-compose up -d

# Local
uvicorn app.main:app --reload --port 8000
```

### Database Migrations

```bash
# Create migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

### Seeding Data

```bash
docker-compose exec backend python scripts/seed_data.py
```

---

## Testing

### Test Users

**All test accounts use the universal password: `test1234`**

| Role | Email | Description |
|------|-------|-------------|
| Admin | admin@reelin.ro | Full administrative access |
| Organizer | organizer@reelin.ro | Can create and manage events |
| Validator | validator@reelin.ro | Can validate catches in events |
| Angler | angler@reelin.ro | Regular user, can enroll in events |

### API Testing

```bash
# Health check
curl http://localhost:8000/health

# Login (all accounts use password: test1234)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@reelin.ro", "password": "test1234"}'
```
