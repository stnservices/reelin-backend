# PostgreSQL Best Practices for FastAPI/SQLAlchemy

This document outlines best practices for efficient database operations in this codebase.

## 1. NEVER Use Loops for Updates/Inserts

### BAD - Individual updates in a loop
```python
# This generates N separate UPDATE queries - VERY SLOW
for i, scoreboard in enumerate(scoreboards, start=1):
    scoreboard.rank = i  # Each assignment = 1 UPDATE query
```

### GOOD - Batch update with raw SQL
```python
# Single query updates ALL rows at once
await db.execute(
    text("""
        UPDATE event_scoreboards es
        SET rank = ranked.new_rank, updated_at = now()
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                ORDER BY total_points DESC
            ) as new_rank
            FROM event_scoreboards
            WHERE event_id = :event_id
        ) ranked
        WHERE es.id = ranked.id AND es.event_id = :event_id
    """),
    {"event_id": event_id}
)
```

## 2. Batch Inserts

### BAD - Individual inserts in a loop
```python
for item in items:
    db.add(MyModel(field=item))  # N insert queries
```

### GOOD - Bulk insert with executemany
```python
# Prepare data as list of dicts
data = [{"field1": x.field1, "field2": x.field2} for x in items]

# Single statement, multiple rows
await db.execute(
    text("""
        INSERT INTO my_table (field1, field2)
        VALUES (:field1, :field2)
    """),
    data  # Pass list of dicts for executemany
)
```

### GOOD - SQLAlchemy bulk insert
```python
from sqlalchemy.dialects.postgresql import insert

stmt = insert(MyModel).values(data)
await db.execute(stmt)
```

## 3. Cache Static/Rarely-Changing Data

### Example: Achievement Definitions Cache
```python
# Module-level cache
_cache: Dict[str, Model] = {}
_cache_time: float = 0
_CACHE_TTL = 300  # 5 minutes

async def get_cached_item(db: AsyncSession, code: str) -> Optional[Model]:
    global _cache, _cache_time

    if not _cache or (time.time() - _cache_time) > _CACHE_TTL:
        result = await db.execute(select(Model))
        _cache = {item.code: item for item in result.scalars().all()}
        _cache_time = time.time()

    return _cache.get(code)
```

## 4. Eager Loading - Avoid N+1 Queries

### BAD - N+1 query problem
```python
events = await db.execute(select(Event))
for event in events:
    # This triggers a NEW query for each event!
    print(event.event_type.name)
```

### GOOD - Eager load with selectinload/joinedload
```python
from sqlalchemy.orm import selectinload, joinedload

events = await db.execute(
    select(Event)
    .options(selectinload(Event.event_type))  # Load in 1 extra query
)
for event in events:
    print(event.event_type.name)  # No additional query
```

**When to use which:**
- `selectinload`: For collections (one-to-many). Uses `SELECT ... WHERE id IN (...)`
- `joinedload`: For single objects (many-to-one). Uses `JOIN`

## 5. Select Only What You Need

### BAD - Fetching entire objects when you only need IDs
```python
result = await db.execute(select(User))  # Fetches ALL columns
user_ids = [u.id for u in result.scalars()]
```

### GOOD - Select specific columns
```python
result = await db.execute(select(User.id))  # Only fetches id column
user_ids = result.scalars().all()
```

## 6. Use Indexes Effectively

### Check your WHERE clauses have indexes
```python
# If you frequently query by code, ensure index exists:
class MyModel(Base):
    code: Mapped[str] = mapped_column(String(50), index=True)
```

### Composite indexes for multi-column queries
```python
__table_args__ = (
    Index('ix_event_user', 'event_id', 'user_id'),
)
```

## 7. Avoid SELECT Inside Loops

### BAD
```python
for user_id in user_ids:
    user = await db.get(User, user_id)  # N queries
```

### GOOD - Batch fetch
```python
result = await db.execute(
    select(User).where(User.id.in_(user_ids))
)
users = {u.id: u for u in result.scalars()}  # 1 query
for user_id in user_ids:
    user = users.get(user_id)
```

## 8. Use Database-Side Operations

### BAD - Fetching data to count in Python
```python
result = await db.execute(select(Model))
count = len(result.scalars().all())  # Fetches ALL rows just to count
```

### GOOD - Count in database
```python
result = await db.execute(select(func.count(Model.id)))
count = result.scalar()  # Only returns the number
```

### Other database functions:
- `func.sum()`, `func.avg()`, `func.max()`, `func.min()`
- `func.coalesce()` for NULL handling
- Window functions: `ROW_NUMBER()`, `RANK()`, `LAG()`, `LEAD()`

## 9. Transaction Management

### Long transactions = Lock contention
```python
# BAD - Long transaction holds locks
async with db.begin():
    for item in thousands_of_items:
        await process(item)  # Locks held for entire duration

# GOOD - Batch commits
batch_size = 100
for i in range(0, len(items), batch_size):
    batch = items[i:i+batch_size]
    for item in batch:
        await process(item)
    await db.commit()  # Release locks periodically
```

## 10. Connection Pool Settings

In `database.py`, ensure proper pool settings:
```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,          # Base connections
    max_overflow=20,       # Extra connections under load
    pool_timeout=30,       # Wait time for connection
    pool_recycle=1800,     # Recycle connections after 30 min
    pool_pre_ping=True,    # Verify connections before use
)
```

## Summary Checklist

Before writing database code, ask:
1. Am I querying in a loop? → Use batch query or cache
2. Am I updating in a loop? → Use batch UPDATE with raw SQL
3. Am I inserting in a loop? → Use bulk insert
4. Do I need all columns? → Select only needed columns
5. Am I accessing relationships? → Use eager loading
6. Is this data static? → Consider caching
7. Do my WHERE columns have indexes? → Add if needed
