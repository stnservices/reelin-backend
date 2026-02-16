"""
TA concurrency stress test — submit/validate with metrics.
Fully API-based. Creates event, enrolls 50 users, generates lineup,
then fires concurrent submit + validate requests across all pairs.

Saves metrics to scripts/ta_stress_metrics.json.

Usage:
    python scripts/test_ta_deadlock.py
"""

import asyncio
import json
import random
import statistics
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────────
API_BASE = "https://www.reelin.ro/api/v1"
ADMIN_EMAIL = "admin@reelin.ro"
ADMIN_PASSWORD = "Admin1234@"
NUM_USERS = 50
USER_EMAIL_TEMPLATE = "user{}@reelin.ro"
USER_PASSWORD = "test1234"
CONCURRENCY_BATCH = 25  # pairs per concurrent batch
# ────────────────────────────────────────────────────────────────

metrics = {
    "test_start": None,
    "test_end": None,
    "num_users": NUM_USERS,
    "submit": {"ok": 0, "errors": 0, "exceptions": 0, "latencies_ms": []},
    "validate": {"ok": 0, "errors": 0, "exceptions": 0, "latencies_ms": []},
    "error_details": [],
}


async def login(client: httpx.AsyncClient, email: str, password: str) -> str | None:
    resp = await client.post(f"{API_BASE}/auth/login", json={
        "email": email, "password": password,
    })
    if resp.status_code != 200:
        return None
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def create_event(client: httpx.AsyncClient, admin_token: str) -> int | None:
    print("\n--- Creating TA event ---")
    h = auth(admin_token)

    resp = await client.get(f"{API_BASE}/events/types", headers=h)
    if resp.status_code != 200:
        print(f"  FAILED to get event types: {resp.status_code}")
        return None

    ta_type = None
    for et in resp.json():
        if et.get("format_code") == "ta" or "trout" in et.get("name", "").lower():
            ta_type = et
            break
    if not ta_type:
        print("  ERROR: No TA event type found")
        return None
    print(f"  TA type: ID {ta_type['id']} ({ta_type['name']})")

    now = datetime.now(timezone.utc)
    resp = await client.post(f"{API_BASE}/events", headers=h, json={
        "name": f"Stress Test {NUM_USERS}u {now.strftime('%H:%M:%S')}",
        "event_type_id": ta_type["id"],
        "start_date": (now + timedelta(minutes=1)).isoformat(),
        "end_date": (now + timedelta(hours=8)).isoformat(),
        "location_name": "Test Lake",
        "max_participants": NUM_USERS + 10,
        "is_team_event": False,
    })
    if resp.status_code not in (200, 201):
        print(f"  FAILED: {resp.status_code} {resp.text[:200]}")
        return None

    event_id = resp.json()["id"]
    print(f"  Created event ID: {event_id}")
    return event_id


async def enroll_users(client, admin_token, event_id, emails):
    print(f"\n--- Enrolling {len(emails)} users ---")
    h = auth(admin_token)
    ok = 0
    for email in emails:
        resp = await client.post(
            f"{API_BASE}/enrollments/admin-enroll/{event_id}",
            headers=h,
            json={"user_email": email, "approve_immediately": True},
        )
        if resp.status_code in (200, 201):
            ok += 1
        else:
            print(f"    WARN {email}: {resp.status_code} {resp.text[:100]}")
    print(f"  Enrolled: {ok}/{len(emails)}")
    return ok


async def configure_and_start(client, admin_token, event_id):
    print("\n--- Configuring event ---")
    h = auth(admin_token)

    resp = await client.post(f"{API_BASE}/ta/events/{event_id}/settings", headers=h, json={
        "event_id": event_id,
        "number_of_legs": 3,
        "has_knockout_stage": False,
        "pairing_algorithm": "round_robin_full",
    })
    if resp.status_code in (200, 201):
        print("  Settings: OK")
    elif resp.status_code == 409:
        await client.put(f"{API_BASE}/ta/events/{event_id}/settings", headers=h, json={
            "number_of_legs": 3, "has_knockout_stage": False, "pairing_algorithm": "round_robin_full",
        })
        print("  Settings: updated existing")
    else:
        print(f"  Settings WARN: {resp.status_code} {resp.text[:150]}")

    resp = await client.post(f"{API_BASE}/ta/events/{event_id}/lineups/generate", headers=h,
                             json={"algorithm": "round_robin_full"})
    print(f"  Lineup: {resp.status_code} — {resp.text[:120]}")

    resp = await client.post(f"{API_BASE}/events/{event_id}/publish", headers=h)
    print(f"  Publish: {resp.status_code}")

    resp = await client.post(f"{API_BASE}/events/{event_id}/start", headers=h)
    print(f"  Start: {resp.status_code}")


async def get_game_cards(client, event_id, token):
    resp = await client.get(f"{API_BASE}/ta/events/{event_id}/game-cards/my", headers=auth(token))
    if resp.status_code != 200:
        return []
    return resp.json().get("items", [])


async def submit_card(client, event_id, card_id, catches, token, label):
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{API_BASE}/ta/events/{event_id}/game-cards/{card_id}/submit",
            headers=auth(token), json={"my_catches": catches},
        )
    except Exception as e:
        ms = (time.monotonic() - t0) * 1000
        metrics["submit"]["exceptions"] += 1
        metrics["submit"]["latencies_ms"].append(ms)
        metrics["error_details"].append({"phase": "submit", "card_id": card_id, "error": str(e)})
        print(f"    [{label}] SUBMIT card {card_id} -> EXCEPTION {e} ({ms:.0f}ms)")
        raise
    ms = (time.monotonic() - t0) * 1000
    metrics["submit"]["latencies_ms"].append(ms)
    if resp.status_code == 200:
        metrics["submit"]["ok"] += 1
    else:
        metrics["submit"]["errors"] += 1
        detail = ""
        try:
            detail = resp.json().get("detail", "")[:80]
        except Exception:
            detail = resp.text[:80]
        metrics["error_details"].append({"phase": "submit", "card_id": card_id, "status": resp.status_code, "detail": detail})
        print(f"    [{label}] SUBMIT card {card_id} -> ERR {resp.status_code} — {detail} ({ms:.0f}ms)")
    return resp


async def validate_card(client, event_id, card_id, token, label):
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{API_BASE}/ta/events/{event_id}/game-cards/{card_id}/validate",
            headers=auth(token), json={"is_valid": True},
        )
    except Exception as e:
        ms = (time.monotonic() - t0) * 1000
        metrics["validate"]["exceptions"] += 1
        metrics["validate"]["latencies_ms"].append(ms)
        metrics["error_details"].append({"phase": "validate", "card_id": card_id, "error": str(e)})
        print(f"    [{label}] VALIDATE card {card_id} -> EXCEPTION {e} ({ms:.0f}ms)")
        raise
    ms = (time.monotonic() - t0) * 1000
    metrics["validate"]["latencies_ms"].append(ms)
    if resp.status_code == 200:
        metrics["validate"]["ok"] += 1
    else:
        metrics["validate"]["errors"] += 1
        detail = ""
        try:
            detail = resp.json().get("detail", "")[:80]
        except Exception:
            detail = resp.text[:80]
        metrics["error_details"].append({"phase": "validate", "card_id": card_id, "status": resp.status_code, "detail": detail})
        print(f"    [{label}] VALIDATE card {card_id} -> ERR {resp.status_code} — {detail} ({ms:.0f}ms)")
    return resp


def find_opponent_pairs(user_cards):
    """Find matching draft card pairs between opponents."""
    pairs = []
    used = set()
    for ea, cards_a in user_cards.items():
        for ca in cards_a:
            if ca["id"] in used or ca["status"] != "draft":
                continue
            if ca.get("is_ghost_opponent") or not ca.get("opponent_id"):
                continue
            for eb, cards_b in user_cards.items():
                if eb == ea:
                    continue
                for cb in cards_b:
                    if cb["id"] in used or cb["status"] != "draft":
                        continue
                    if (cb["user_id"] == ca["opponent_id"]
                            and cb.get("opponent_id") == ca["user_id"]
                            and cb["leg_number"] == ca["leg_number"]):
                        pairs.append((ea, ca, eb, cb))
                        used.add(ca["id"])
                        used.add(cb["id"])
                        break
                else:
                    continue
                break
    return pairs


def find_submitted_pairs(user_cards):
    """Find matching submitted card pairs ready for cross-validation."""
    pairs = []
    used = set()
    for ea, cards_a in user_cards.items():
        for ca in cards_a:
            if ca["id"] in used or ca["status"] != "submitted":
                continue
            if ca.get("is_ghost_opponent") or not ca.get("opponent_id"):
                continue
            for eb, cards_b in user_cards.items():
                if eb == ea:
                    continue
                for cb in cards_b:
                    if cb["id"] in used or cb["status"] != "submitted":
                        continue
                    if (cb["user_id"] == ca["opponent_id"]
                            and cb.get("opponent_id") == ca["user_id"]
                            and cb["leg_number"] == ca["leg_number"]):
                        pairs.append((ea, ca, eb, cb))
                        used.add(ca["id"])
                        used.add(cb["id"])
                        break
                else:
                    continue
                break
    return pairs


def latency_stats(latencies):
    if not latencies:
        return {}
    s = sorted(latencies)
    return {
        "count": len(s),
        "min_ms": round(s[0], 1),
        "p50_ms": round(statistics.median(s), 1),
        "p90_ms": round(s[int(len(s) * 0.9)], 1),
        "p99_ms": round(s[int(len(s) * 0.99)], 1) if len(s) >= 100 else round(s[-1], 1),
        "max_ms": round(s[-1], 1),
        "mean_ms": round(statistics.mean(s), 1),
    }


async def run_stress_test(client, event_id, tokens):
    print("\n" + "=" * 60)
    print("Phase 3: Concurrent Submit + Validate Stress Test")
    print("=" * 60)

    metrics["test_start"] = datetime.now(timezone.utc).isoformat()

    # Fetch all game cards
    print("\nFetching game cards for all users...")
    user_cards = {}
    total_cards = 0
    for email, token in tokens.items():
        cards = await get_game_cards(client, event_id, token)
        user_cards[email] = cards
        total_cards += len(cards)
    print(f"  Total cards: {total_cards} across {len(tokens)} users")
    metrics["total_cards"] = total_cards

    # ── SUBMIT PHASE ──
    pairs = find_opponent_pairs(user_cards)
    print(f"\nFound {len(pairs)} draft opponent pairs to submit")
    metrics["total_pairs"] = len(pairs)

    if not pairs:
        print("ERROR: No opponent pairs found")
        return

    # Process submits in batches
    print(f"\n--- Submit Phase (batches of {CONCURRENCY_BATCH} pairs) ---")
    submit_wall_start = time.monotonic()
    for batch_idx in range(0, len(pairs), CONCURRENCY_BATCH):
        batch = pairs[batch_idx:batch_idx + CONCURRENCY_BATCH]
        tasks = []
        for i, (ea, ca, eb, cb) in enumerate(batch):
            idx = batch_idx + i + 1
            catches_a = random.randint(0, 12)
            catches_b = random.randint(0, 12)
            tasks.append(submit_card(client, event_id, ca["id"], catches_a, tokens[ea], f"S{idx}A"))
            tasks.append(submit_card(client, event_id, cb["id"], catches_b, tokens[eb], f"S{idx}B"))

        t0 = time.monotonic()
        await asyncio.gather(*tasks, return_exceptions=True)
        batch_ms = (time.monotonic() - t0) * 1000
        print(f"  Batch {batch_idx // CONCURRENCY_BATCH + 1}: {len(batch)} pairs, {batch_ms:.0f}ms wall")

    submit_wall_ms = (time.monotonic() - submit_wall_start) * 1000
    print(f"\n  Submit phase total wall time: {submit_wall_ms:.0f}ms")
    print(f"  Submit OK: {metrics['submit']['ok']} | Errors: {metrics['submit']['errors']} | Exceptions: {metrics['submit']['exceptions']}")

    await asyncio.sleep(0.5)

    # ── VALIDATE PHASE ──
    print(f"\n--- Validate Phase (concurrent cross-validation) ---")

    # Re-fetch cards to get updated statuses
    for email in tokens:
        user_cards[email] = await get_game_cards(client, event_id, tokens[email])

    submitted_pairs = find_submitted_pairs(user_cards)
    print(f"  Found {len(submitted_pairs)} submitted pairs ready for validation")

    validate_wall_start = time.monotonic()
    for batch_idx in range(0, len(submitted_pairs), CONCURRENCY_BATCH):
        batch = submitted_pairs[batch_idx:batch_idx + CONCURRENCY_BATCH]
        tasks = []
        for i, (ea, ca, eb, cb) in enumerate(batch):
            idx = batch_idx + i + 1
            # Each user validates the OTHER user's card (cross-validation)
            tasks.append(validate_card(client, event_id, cb["id"], tokens[ea], f"V{idx}A->B"))
            tasks.append(validate_card(client, event_id, ca["id"], tokens[eb], f"V{idx}B->A"))

        t0 = time.monotonic()
        await asyncio.gather(*tasks, return_exceptions=True)
        batch_ms = (time.monotonic() - t0) * 1000
        print(f"  Batch {batch_idx // CONCURRENCY_BATCH + 1}: {len(batch)} pairs, {batch_ms:.0f}ms wall")

    validate_wall_ms = (time.monotonic() - validate_wall_start) * 1000
    print(f"\n  Validate phase total wall time: {validate_wall_ms:.0f}ms")
    print(f"  Validate OK: {metrics['validate']['ok']} | Errors: {metrics['validate']['errors']} | Exceptions: {metrics['validate']['exceptions']}")

    metrics["test_end"] = datetime.now(timezone.utc).isoformat()
    metrics["submit_wall_ms"] = round(submit_wall_ms, 1)
    metrics["validate_wall_ms"] = round(validate_wall_ms, 1)

    # ── SUMMARY ──
    total_ok = metrics["submit"]["ok"] + metrics["validate"]["ok"]
    total_err = metrics["submit"]["errors"] + metrics["validate"]["errors"]
    total_exc = metrics["submit"]["exceptions"] + metrics["validate"]["exceptions"]
    total_reqs = total_ok + total_err + total_exc

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  Users: {NUM_USERS}")
    print(f"  Total pairs processed: {len(pairs)} submit + {len(submitted_pairs)} validate")
    print(f"  Total requests: {total_reqs}")
    print(f"  OK: {total_ok} | Errors: {total_err} | Exceptions: {total_exc}")

    submit_stats = latency_stats(metrics["submit"]["latencies_ms"])
    validate_stats = latency_stats(metrics["validate"]["latencies_ms"])

    if submit_stats:
        print(f"\n  Submit latency:   p50={submit_stats['p50_ms']}ms  p90={submit_stats['p90_ms']}ms  max={submit_stats['max_ms']}ms")
    if validate_stats:
        print(f"  Validate latency: p50={validate_stats['p50_ms']}ms  p90={validate_stats['p90_ms']}ms  max={validate_stats['max_ms']}ms")

    if total_err == 0 and total_exc == 0:
        print("\n  ALL PASSED — no errors detected")
    else:
        print(f"\n  ISSUES DETECTED — {total_err} errors, {total_exc} exceptions")
        for ed in metrics["error_details"][:10]:
            print(f"    {ed}")

    print("=" * 60)

    # Save metrics
    metrics["submit"]["latency_stats"] = submit_stats
    metrics["validate"]["latency_stats"] = validate_stats
    metrics["summary"] = {
        "total_requests": total_reqs,
        "total_ok": total_ok,
        "total_errors": total_err,
        "total_exceptions": total_exc,
        "success_rate": round(total_ok / total_reqs * 100, 2) if total_reqs else 0,
    }

    out_path = Path(__file__).parent / "ta_stress_metrics.json"
    # Remove raw latencies from saved file (keep stats only)
    save_metrics = {**metrics}
    save_metrics["submit"] = {k: v for k, v in metrics["submit"].items() if k != "latencies_ms"}
    save_metrics["validate"] = {k: v for k, v in metrics["validate"].items() if k != "latencies_ms"}
    with open(out_path, "w") as f:
        json.dump(save_metrics, f, indent=2, default=str)
    print(f"\nMetrics saved to {out_path}")


async def main():
    emails = [USER_EMAIL_TEMPLATE.format(i) for i in range(1, NUM_USERS + 1)]

    async with httpx.AsyncClient(timeout=60.0) as client:
        print("=" * 60)
        print(f"TA Stress Test — {NUM_USERS} users")
        print("=" * 60)

        print("\nLogging in admin...")
        admin_token = await login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        if not admin_token:
            print("ERROR: Admin login failed"); sys.exit(1)
        print("  OK")

        # Create event
        event_id = await create_event(client, admin_token)
        if not event_id:
            sys.exit(1)
        metrics["event_id"] = event_id

        # Enroll users
        enrolled = await enroll_users(client, admin_token, event_id, emails)
        metrics["enrolled_users"] = enrolled

        # Configure + start
        print("\n" + "=" * 60)
        print("Phase 2: Configure & Start")
        print("=" * 60)
        await configure_and_start(client, admin_token, event_id)

        # Login test users
        print("\nLogging in test users...")
        tokens = {}
        for email in emails:
            token = await login(client, email, USER_PASSWORD)
            if token:
                tokens[email] = token
        print(f"  {len(tokens)}/{len(emails)} logged in")
        metrics["logged_in_users"] = len(tokens)

        if len(tokens) < 2:
            print("ERROR: Need at least 2 users"); sys.exit(1)

        # Run stress test
        await run_stress_test(client, event_id, tokens)


if __name__ == "__main__":
    asyncio.run(main())
