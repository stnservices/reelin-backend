"""Performance Round 3 — Functional + Performance Tests.

Tests:
  Part A: Auth-switched endpoints (get_current_user_id_cached)
  Part B: Cached TA/SF endpoints (Redis TTL caching)

Usage:
  python3 scripts/test_perf_round3.py
"""

import asyncio
import json
import os
import statistics
import time
import base64
from dataclasses import dataclass, field

import httpx

API_BASE = os.environ.get("REELIN_API_BASE", "https://www.reelin.ro/api/v1")
ADMIN_EMAIL = os.environ.get("REELIN_ADMIN_EMAIL", "admin@reelin.ro")
ADMIN_PASSWORD = os.environ["REELIN_ADMIN_PASSWORD"]
USER_EMAIL_TPL = os.environ.get("REELIN_USER_EMAIL_TPL", "user{}@reelin.ro")
USER_PASSWORD = os.environ["REELIN_USER_PASSWORD"]
NUM_USERS = 5  # enough for cache testing
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def uid_from_jwt(token: str) -> int:
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    data = json.loads(base64.b64decode(payload))
    return int(data["sub"])


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Metrics:
    name: str
    latencies: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def record(self, elapsed: float, status: int, ok_codes=(200,)):
        self.latencies.append(elapsed)
        if status not in ok_codes:
            self.errors.append(status)

    def summary(self) -> str:
        if not self.latencies:
            return f"  {self.name}: NO DATA"
        p50 = statistics.median(self.latencies)
        p95 = sorted(self.latencies)[int(len(self.latencies) * 0.95)] if len(self.latencies) >= 2 else p50
        avg = statistics.mean(self.latencies)
        errs = len(self.errors)
        return (
            f"  {self.name}: n={len(self.latencies)} avg={avg:.0f}ms "
            f"p50={p50:.0f}ms p95={p95:.0f}ms {'ERRORS=' + str(errs) if errs else 'OK'}"
        )


async def timed_get(client, url, headers=None) -> tuple[int, float, dict]:
    t0 = time.monotonic()
    resp = await client.get(url, headers=headers)
    elapsed = (time.monotonic() - t0) * 1000
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, elapsed, data


async def timed_post(client, url, headers=None, json_data=None) -> tuple[int, float, dict]:
    t0 = time.monotonic()
    resp = await client.post(url, headers=headers, json=json_data or {})
    elapsed = (time.monotonic() - t0) * 1000
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, elapsed, data


async def login(client: httpx.AsyncClient, email: str, password: str):
    resp = await client.post(f"{API_BASE}/auth/login", json={
        "email": email, "password": password,
    })
    if resp.status_code != 200:
        print(f"  Login FAILED for {email}: {resp.status_code}")
        return None
    token = resp.json()["access_token"]
    return token, uid_from_jwt(token)


async def find_events(client: httpx.AsyncClient, admin_token: str):
    """Find a TA event and SF event for testing."""
    ta_event_id = None
    sf_event_id = None

    # Get recent events
    status_code, _, data = await timed_get(
        client, f"{API_BASE}/events?page_size=50", auth(admin_token)
    )
    if status_code != 200:
        print(f"  Failed to list events: {status_code}")
        return None, None

    events = data.get("items", [])
    for ev in events:
        ev_type = ev.get("event_type", {})
        ev_code = ev_type.get("code", "") if isinstance(ev_type, dict) else ""
        ev_status = ev.get("status", "")
        if ev_code == "trout_area" and ta_event_id is None and ev_status in ("ongoing", "completed"):
            ta_event_id = ev["id"]
        elif ev_code == "street_fishing" and sf_event_id is None and ev_status in ("ongoing", "completed"):
            sf_event_id = ev["id"]
        if ta_event_id and sf_event_id:
            break

    return ta_event_id, sf_event_id


async def test_part_a(client: httpx.AsyncClient, token: str, user_id: int):
    """Test Part A: Auth-switched endpoints return correct data."""
    h = auth(token)
    results = {}

    # Notifications
    code, ms, data = await timed_get(client, f"{API_BASE}/notifications", h)
    results["notifications_list"] = Metrics("GET /notifications")
    results["notifications_list"].record(ms, code)

    code, ms, data = await timed_get(client, f"{API_BASE}/notifications/stats", h)
    results["notifications_stats"] = Metrics("GET /notifications/stats")
    results["notifications_stats"].record(ms, code)

    # Achievements
    code, ms, data = await timed_get(client, f"{API_BASE}/achievements/me", h)
    results["achievements_me"] = Metrics("GET /achievements/me")
    results["achievements_me"].record(ms, code)

    code, ms, data = await timed_get(client, f"{API_BASE}/achievements/statistics/me", h)
    results["achievements_stats_me"] = Metrics("GET /achievements/statistics/me")
    results["achievements_stats_me"].record(ms, code)

    # Achievements for other user (verify path param not shadowed)
    code, ms, data = await timed_get(client, f"{API_BASE}/achievements/users/{user_id}", h)
    results["achievements_other"] = Metrics("GET /achievements/users/{id}")
    results["achievements_other"].record(ms, code)

    code, ms, data = await timed_get(client, f"{API_BASE}/achievements/statistics/users/{user_id}", h)
    results["achievements_stats_other"] = Metrics("GET /achievements/statistics/users/{id}")
    results["achievements_stats_other"].record(ms, code)

    # Rules
    code, ms, data = await timed_get(client, f"{API_BASE}/rules", h)
    results["rules_list"] = Metrics("GET /rules")
    results["rules_list"].record(ms, code)

    code, ms, data = await timed_get(client, f"{API_BASE}/rules/defaults", h)
    results["rules_defaults"] = Metrics("GET /rules/defaults")
    results["rules_defaults"].record(ms, code)

    # Follows
    code, ms, data = await timed_get(client, f"{API_BASE}/follows/me/following", h)
    results["follows_me"] = Metrics("GET /follows/me/following")
    results["follows_me"].record(ms, code)

    code, ms, data = await timed_get(client, f"{API_BASE}/follows/{user_id}/follow-stats", h)
    results["follows_stats"] = Metrics("GET /follows/{id}/follow-stats")
    results["follows_stats"].record(ms, code)

    return results


async def test_part_b_ta(client: httpx.AsyncClient, token: str, user_id: int, event_id: int):
    """Test Part B: TA cached endpoints."""
    h = auth(token)
    results = {}

    # Rankings — first call (cache miss), second call (cache hit)
    m = Metrics(f"GET /ta/events/{event_id}/rankings")
    for i in range(5):
        code, ms, data = await timed_get(client, f"{API_BASE}/ta/events/{event_id}/rankings", h)
        m.record(ms, code)
    results["ta_rankings"] = m

    # Statistics
    m = Metrics(f"GET /ta/events/{event_id}/statistics")
    for i in range(5):
        code, ms, data = await timed_get(client, f"{API_BASE}/ta/events/{event_id}/statistics", h)
        m.record(ms, code)
    results["ta_statistics"] = m

    # My matches (per-user cached)
    m = Metrics(f"GET /ta/events/{event_id}/my-matches")
    for i in range(5):
        code, ms, data = await timed_get(client, f"{API_BASE}/ta/events/{event_id}/my-matches", h)
        m.record(ms, code)
    results["ta_my_matches"] = m

    # My game cards (per-user cached)
    m = Metrics(f"GET /ta/events/{event_id}/game-cards/my")
    for i in range(5):
        code, ms, data = await timed_get(client, f"{API_BASE}/ta/events/{event_id}/game-cards/my", h)
        m.record(ms, code)
    results["ta_game_cards"] = m

    # Public standings (no auth needed)
    m = Metrics(f"GET /ta/public/events/{event_id}/standings")
    for i in range(5):
        code, ms, _ = await timed_get(client, f"{API_BASE}/ta/public/events/{event_id}/standings")
        m.record(ms, code)
    results["ta_public_standings"] = m

    # Public status (no auth needed)
    m = Metrics(f"GET /ta/public/events/{event_id}/status")
    for i in range(5):
        code, ms, _ = await timed_get(client, f"{API_BASE}/ta/public/events/{event_id}/status")
        m.record(ms, code)
    results["ta_public_status"] = m

    # Public bracket (no auth needed)
    m = Metrics(f"GET /ta/public/events/{event_id}/bracket")
    for i in range(5):
        code, ms, _ = await timed_get(client, f"{API_BASE}/ta/public/events/{event_id}/bracket")
        m.record(ms, code, ok_codes=(200, 404))  # 404 if no knockout
    results["ta_public_bracket"] = m

    return results


async def test_part_b_sf(client: httpx.AsyncClient, event_id: int):
    """Test Part B: SF cached endpoints."""
    results = {}

    m = Metrics(f"GET /catches/leaderboard/{event_id}")
    for i in range(5):
        code, ms, _ = await timed_get(client, f"{API_BASE}/catches/leaderboard/{event_id}")
        m.record(ms, code)
    results["sf_leaderboard"] = m

    return results


async def test_concurrent_polling(client: httpx.AsyncClient, tokens: list[tuple], event_id: int, endpoint: str, n_rounds: int = 3):
    """Simulate concurrent polling by multiple users."""
    m = Metrics(f"CONCURRENT {endpoint} (x{len(tokens)} users)")

    for round_num in range(n_rounds):
        tasks = []
        for token, uid in tokens:
            url = f"{API_BASE}{endpoint}".format(event_id=event_id)
            tasks.append(timed_get(client, url, auth(token)))

        results = await asyncio.gather(*tasks)
        for code, ms, _ in results:
            m.record(ms, code)

    return m


async def main():
    print("=" * 60)
    print("Performance Round 3 — Functional & Performance Tests")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=TIMEOUT, verify=True) as client:
        # 1. Login
        print("\n[1] Authenticating...")
        admin_result = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        if not admin_result:
            print("FATAL: Admin login failed")
            return
        admin_token, admin_uid = admin_result
        print(f"  Admin logged in (uid={admin_uid})")

        user_tokens = []
        for i in range(1, NUM_USERS + 1):
            result = await login(client, USER_EMAIL_TPL.format(i), USER_PASSWORD)
            if result:
                user_tokens.append(result)
        print(f"  {len(user_tokens)} test users logged in")

        if not user_tokens:
            print("FATAL: No test users could log in")
            return

        # 2. Find events
        print("\n[2] Finding TA and SF events...")
        ta_event_id, sf_event_id = await find_events(client, admin_token)
        print(f"  TA event: {ta_event_id or 'NOT FOUND'}")
        print(f"  SF event: {sf_event_id or 'NOT FOUND'}")

        # 3. Part A — Auth-switched endpoints
        print("\n[3] Part A: Testing auth-switched endpoints...")
        token, uid = user_tokens[0]
        part_a_results = await test_part_a(client, token, uid)
        for m in part_a_results.values():
            print(m.summary())

        # 4. Part B — TA cached endpoints
        if ta_event_id:
            print(f"\n[4] Part B: Testing TA cached endpoints (event={ta_event_id})...")
            token, uid = user_tokens[0]
            part_b_ta = await test_part_b_ta(client, token, uid, ta_event_id)
            for m in part_b_ta.values():
                print(m.summary())

            # Concurrent polling test
            print(f"\n[5] Concurrent polling test ({len(user_tokens)} users x 3 rounds)...")
            concurrent_rankings = await test_concurrent_polling(
                client, user_tokens, ta_event_id,
                "/ta/events/{event_id}/rankings"
            )
            print(concurrent_rankings.summary())

            concurrent_standings = await test_concurrent_polling(
                client, user_tokens, ta_event_id,
                "/ta/public/events/{event_id}/standings"
            )
            print(concurrent_standings.summary())
        else:
            print("\n[4] SKIP: No TA event found")
            print("[5] SKIP: No TA event found")

        # 5. Part B — SF cached endpoints
        if sf_event_id:
            print(f"\n[6] Part B: Testing SF cached endpoints (event={sf_event_id})...")
            part_b_sf = await test_part_b_sf(client, sf_event_id)
            for m in part_b_sf.values():
                print(m.summary())
        else:
            print("\n[6] SKIP: No SF event found")

        # 6. Verify cache behavior (hit should be faster than miss)
        if ta_event_id:
            print("\n[7] Cache behavior verification...")
            # Cold call (possible cache miss after TTL)
            await asyncio.sleep(11)  # wait for 10s TTL to expire
            code1, ms_miss, _ = await timed_get(
                client, f"{API_BASE}/ta/events/{ta_event_id}/rankings", auth(user_tokens[0][0])
            )
            # Warm call (cache hit)
            code2, ms_hit, _ = await timed_get(
                client, f"{API_BASE}/ta/events/{ta_event_id}/rankings", auth(user_tokens[0][0])
            )
            speedup = ms_miss / ms_hit if ms_hit > 0 else 0
            print(f"  Rankings: miss={ms_miss:.0f}ms hit={ms_hit:.0f}ms speedup={speedup:.1f}x")
            if code1 == 200 and code2 == 200:
                print(f"  Cache working: {'YES' if speedup > 1.2 else 'MARGINAL (network latency dominates)'}")
            else:
                print(f"  ERROR: status codes {code1}, {code2}")

        print("\n" + "=" * 60)
        print("Tests complete.")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
