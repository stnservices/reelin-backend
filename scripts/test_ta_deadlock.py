"""
TA concurrency stress test — leg-by-leg submit+validate with metrics,
deadlock detection, max-pressure mode, and post-test correctness verification.

Fully API-based. Creates event, enrolls users, generates lineup,
then processes each leg: submit both sides → validate both sides.

Saves metrics to scripts/ta_stress_metrics.json.

Usage:
    python scripts/test_ta_deadlock.py
    python scripts/test_ta_deadlock.py --num-users 10 --num-legs 2
    python scripts/test_ta_deadlock.py --max-pressure
    python scripts/test_ta_deadlock.py --skip-verify
"""

import argparse
import asyncio
import json
import random
import statistics
import time
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx


# ── Config ──────────────────────────────────────────────────────


@dataclass
class Config:
    api_base: str = "https://www.reelin.ro/api/v1"
    admin_email: str = "admin@reelin.ro"
    admin_password: str = "Admin1234@"
    num_users: int = 33
    user_email_template: str = "user{}@reelin.ro"
    user_password: str = "test1234"
    concurrency_batch: int = 15
    num_legs: int = 3
    max_pressure: bool = False
    skip_verify: bool = False


def parse_args() -> Config:
    p = argparse.ArgumentParser(description="TA concurrency stress test")
    p.add_argument("--api-base", default=Config.api_base)
    p.add_argument("--admin-email", default=Config.admin_email)
    p.add_argument("--admin-password", default=Config.admin_password)
    p.add_argument("--num-users", type=int, default=Config.num_users)
    p.add_argument("--user-email-template", default=Config.user_email_template)
    p.add_argument("--user-password", default=Config.user_password)
    p.add_argument("--concurrency-batch", type=int, default=Config.concurrency_batch)
    p.add_argument("--num-legs", type=int, default=Config.num_legs)
    p.add_argument("--max-pressure", action="store_true",
                   help="Fire ALL validate calls simultaneously (no batching)")
    p.add_argument("--skip-verify", action="store_true",
                   help="Skip post-test correctness checks")
    args = p.parse_args()
    return Config(
        api_base=args.api_base,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
        num_users=args.num_users,
        user_email_template=args.user_email_template,
        user_password=args.user_password,
        concurrency_batch=args.concurrency_batch,
        num_legs=args.num_legs,
        max_pressure=args.max_pressure,
        skip_verify=args.skip_verify,
    )


# ── Metrics ─────────────────────────────────────────────────────


def make_metrics(cfg: Config) -> dict:
    return {
        "test_start": None,
        "test_end": None,
        "num_users": cfg.num_users,
        "legs": {},
        "submit": {"ok": 0, "errors": 0, "exceptions": 0, "latencies_ms": []},
        "validate": {"ok": 0, "errors": 0, "exceptions": 0, "latencies_ms": []},
        "error_details": [],
        "deadlocks": {"submit": 0, "validate": 0, "details": []},
    }


# ── Helpers ─────────────────────────────────────────────────────


async def login(client: httpx.AsyncClient, cfg: Config, email: str, password: str) -> str | None:
    resp = await client.post(f"{cfg.api_base}/auth/login", json={
        "email": email, "password": password,
    })
    if resp.status_code != 200:
        return None
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def refresh_tokens_parallel(client: httpx.AsyncClient, cfg: Config, emails: list[str]) -> dict[str, str]:
    """Login all users in parallel (~33x faster than serial)."""
    async def _login_one(email: str):
        token = await login(client, cfg, email, cfg.user_password)
        return email, token

    results = await asyncio.gather(*[_login_one(e) for e in emails], return_exceptions=True)
    tokens = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        email, token = r
        if token:
            tokens[email] = token
    return tokens


async def create_event(client: httpx.AsyncClient, cfg: Config, admin_token: str) -> int | None:
    print("\n--- Creating TA event ---")
    h = auth(admin_token)

    resp = await client.get(f"{cfg.api_base}/events/types", headers=h)
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
    mode_label = "max-pressure" if cfg.max_pressure else "batched"
    resp = await client.post(f"{cfg.api_base}/events", headers=h, json={
        "name": f"Stress {cfg.num_users}u {mode_label} {now.strftime('%H:%M:%S')}",
        "event_type_id": ta_type["id"],
        "start_date": (now + timedelta(minutes=1)).isoformat(),
        "end_date": (now + timedelta(hours=8)).isoformat(),
        "location_name": "Test Lake",
        "max_participants": cfg.num_users + 10,
        "is_team_event": False,
        "is_test": True,
    })
    if resp.status_code not in (200, 201):
        print(f"  FAILED: {resp.status_code} {resp.text[:200]}")
        return None

    event_id = resp.json()["id"]
    print(f"  Created event ID: {event_id}")
    return event_id


async def enroll_users(client, cfg: Config, admin_token, event_id, emails):
    print(f"\n--- Enrolling {len(emails)} users ---")
    h = auth(admin_token)
    ok = 0
    for email in emails:
        resp = await client.post(
            f"{cfg.api_base}/enrollments/admin-enroll/{event_id}",
            headers=h,
            json={"user_email": email, "approve_immediately": True},
        )
        if resp.status_code in (200, 201):
            ok += 1
        else:
            print(f"    WARN {email}: {resp.status_code} {resp.text[:100]}")
    print(f"  Enrolled: {ok}/{len(emails)}")
    return ok


async def configure_and_start(client, cfg: Config, admin_token, event_id):
    print("\n--- Configuring event ---")
    h = auth(admin_token)

    resp = await client.post(f"{cfg.api_base}/ta/events/{event_id}/settings", headers=h, json={
        "event_id": event_id,
        "number_of_legs": cfg.num_legs,
        "has_knockout_stage": False,
        "pairing_algorithm": "round_robin_half",
    })
    if resp.status_code in (200, 201):
        print("  Settings: OK")
    elif resp.status_code == 409:
        await client.put(f"{cfg.api_base}/ta/events/{event_id}/settings", headers=h, json={
            "number_of_legs": cfg.num_legs, "has_knockout_stage": False, "pairing_algorithm": "round_robin_half",
        })
        print("  Settings: updated existing")
    else:
        print(f"  Settings WARN: {resp.status_code} {resp.text[:150]}")

    resp = await client.post(f"{cfg.api_base}/ta/events/{event_id}/lineups/generate", headers=h,
                             json={"algorithm": "round_robin_half"})
    print(f"  Lineup: {resp.status_code} — {resp.text[:120]}")

    resp = await client.post(f"{cfg.api_base}/events/{event_id}/publish", headers=h)
    print(f"  Publish: {resp.status_code}")

    resp = await client.post(f"{cfg.api_base}/events/{event_id}/start", headers=h)
    print(f"  Start: {resp.status_code}")


async def get_game_cards(client, cfg: Config, event_id, token):
    resp = await client.get(f"{cfg.api_base}/ta/events/{event_id}/game-cards/my", headers=auth(token))
    if resp.status_code != 200:
        return []
    return resp.json().get("items", [])


async def submit_card(client, cfg: Config, event_id, card_id, catches, token, label, metrics):
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{cfg.api_base}/ta/events/{event_id}/game-cards/{card_id}/submit",
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
        # Deadlock detection
        if resp.status_code >= 500 and "deadlock" in detail.lower():
            metrics["deadlocks"]["submit"] += 1
            metrics["deadlocks"]["details"].append({"phase": "submit", "card_id": card_id, "detail": detail})
        metrics["error_details"].append({"phase": "submit", "card_id": card_id, "status": resp.status_code, "detail": detail})
        print(f"    [{label}] SUBMIT card {card_id} -> ERR {resp.status_code} — {detail} ({ms:.0f}ms)")
    return resp


async def validate_card(client, cfg: Config, event_id, card_id, token, label, metrics):
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{cfg.api_base}/ta/events/{event_id}/game-cards/{card_id}/validate",
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
        # Deadlock detection
        if resp.status_code >= 500 and "deadlock" in detail.lower():
            metrics["deadlocks"]["validate"] += 1
            metrics["deadlocks"]["details"].append({"phase": "validate", "card_id": card_id, "detail": detail})
        metrics["error_details"].append({"phase": "validate", "card_id": card_id, "status": resp.status_code, "detail": detail})
        print(f"    [{label}] VALIDATE card {card_id} -> ERR {resp.status_code} — {detail} ({ms:.0f}ms)")
    return resp


def find_pairs_for_leg(user_cards, leg_number, status_filter="draft"):
    """Find matching card pairs between opponents for a specific leg."""
    pairs = []
    used = set()
    for ea, cards_a in user_cards.items():
        for ca in cards_a:
            if ca["id"] in used or ca["status"] != status_filter:
                continue
            if ca["leg_number"] != leg_number:
                continue
            if ca.get("is_ghost_opponent") or not ca.get("opponent_id"):
                continue
            for eb, cards_b in user_cards.items():
                if eb == ea:
                    continue
                for cb in cards_b:
                    if cb["id"] in used or cb["status"] != status_filter:
                        continue
                    if cb["leg_number"] != leg_number:
                        continue
                    if (cb["user_id"] == ca["opponent_id"]
                            and cb.get("opponent_id") == ca["user_id"]):
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


# ── Leg Processors ──────────────────────────────────────────────


async def process_leg(client, cfg: Config, event_id, tokens, user_cards, leg_number, metrics):
    """Process a single leg: submit all pairs (batched), then validate all pairs (batched)."""
    print(f"\n{'─' * 50}")
    print(f"  LEG {leg_number}")
    print(f"{'─' * 50}")

    leg_metrics = {"submit_ok": 0, "submit_err": 0, "validate_ok": 0, "validate_err": 0}

    pairs = find_pairs_for_leg(user_cards, leg_number, "draft")
    print(f"  Draft pairs: {len(pairs)}")

    if not pairs:
        print(f"  No pairs for leg {leg_number}, skipping")
        return leg_metrics

    # ── SUBMIT both sides concurrently (batched) ──
    print(f"  Submitting ({len(pairs)} pairs)...")
    submit_start = time.monotonic()
    sub_ok_before = metrics["submit"]["ok"]
    sub_err_before = metrics["submit"]["errors"]

    for batch_idx in range(0, len(pairs), cfg.concurrency_batch):
        batch = pairs[batch_idx:batch_idx + cfg.concurrency_batch]
        tasks = []
        for i, (ea, ca, eb, cb) in enumerate(batch):
            idx = batch_idx + i + 1
            catches_a = random.randint(0, 12)
            catches_b = random.randint(0, 12)
            tasks.append(submit_card(client, cfg, event_id, ca["id"], catches_a, tokens[ea], f"L{leg_number}S{idx}A", metrics))
            tasks.append(submit_card(client, cfg, event_id, cb["id"], catches_b, tokens[eb], f"L{leg_number}S{idx}B", metrics))
        await asyncio.gather(*tasks, return_exceptions=True)

    submit_ms = (time.monotonic() - submit_start) * 1000
    leg_metrics["submit_ok"] = metrics["submit"]["ok"] - sub_ok_before
    leg_metrics["submit_err"] = metrics["submit"]["errors"] - sub_err_before
    print(f"  Submit: {leg_metrics['submit_ok']} OK, {leg_metrics['submit_err']} errors ({submit_ms:.0f}ms)")

    await asyncio.sleep(0.3)

    # ── Re-fetch cards and VALIDATE both sides concurrently (batched) ──
    print(f"  Re-fetching cards...")
    for email in tokens:
        user_cards[email] = await get_game_cards(client, cfg, event_id, tokens[email])

    submitted_pairs = find_pairs_for_leg(user_cards, leg_number, "submitted")
    print(f"  Submitted pairs ready for validation: {len(submitted_pairs)}")

    if submitted_pairs:
        print(f"  Validating ({len(submitted_pairs)} pairs)...")
        validate_start = time.monotonic()
        val_ok_before = metrics["validate"]["ok"]
        val_err_before = metrics["validate"]["errors"]

        for batch_idx in range(0, len(submitted_pairs), cfg.concurrency_batch):
            batch = submitted_pairs[batch_idx:batch_idx + cfg.concurrency_batch]
            tasks = []
            for i, (ea, ca, eb, cb) in enumerate(batch):
                idx = batch_idx + i + 1
                tasks.append(validate_card(client, cfg, event_id, cb["id"], tokens[ea], f"L{leg_number}V{idx}A->B", metrics))
                tasks.append(validate_card(client, cfg, event_id, ca["id"], tokens[eb], f"L{leg_number}V{idx}B->A", metrics))
            await asyncio.gather(*tasks, return_exceptions=True)

        validate_ms = (time.monotonic() - validate_start) * 1000
        leg_metrics["validate_ok"] = metrics["validate"]["ok"] - val_ok_before
        leg_metrics["validate_err"] = metrics["validate"]["errors"] - val_err_before
        print(f"  Validate: {leg_metrics['validate_ok']} OK, {leg_metrics['validate_err']} errors ({validate_ms:.0f}ms)")

    # Re-fetch for next leg
    for email in tokens:
        user_cards[email] = await get_game_cards(client, cfg, event_id, tokens[email])

    return leg_metrics


async def process_leg_max_pressure(client, cfg: Config, event_id, tokens, user_cards, leg_number, metrics):
    """Max-pressure mode: submit batched, validate ALL at once (no batching)."""
    print(f"\n{'─' * 50}")
    print(f"  LEG {leg_number} [MAX PRESSURE]")
    print(f"{'─' * 50}")

    leg_metrics = {"submit_ok": 0, "submit_err": 0, "validate_ok": 0, "validate_err": 0}

    pairs = find_pairs_for_leg(user_cards, leg_number, "draft")
    print(f"  Draft pairs: {len(pairs)}")

    if not pairs:
        print(f"  No pairs for leg {leg_number}, skipping")
        return leg_metrics

    # ── SUBMIT both sides (still batched — submission order doesn't cause contention) ──
    print(f"  Submitting ({len(pairs)} pairs)...")
    submit_start = time.monotonic()
    sub_ok_before = metrics["submit"]["ok"]
    sub_err_before = metrics["submit"]["errors"]

    for batch_idx in range(0, len(pairs), cfg.concurrency_batch):
        batch = pairs[batch_idx:batch_idx + cfg.concurrency_batch]
        tasks = []
        for i, (ea, ca, eb, cb) in enumerate(batch):
            idx = batch_idx + i + 1
            catches_a = random.randint(0, 12)
            catches_b = random.randint(0, 12)
            tasks.append(submit_card(client, cfg, event_id, ca["id"], catches_a, tokens[ea], f"L{leg_number}S{idx}A", metrics))
            tasks.append(submit_card(client, cfg, event_id, cb["id"], catches_b, tokens[eb], f"L{leg_number}S{idx}B", metrics))
        await asyncio.gather(*tasks, return_exceptions=True)

    submit_ms = (time.monotonic() - submit_start) * 1000
    leg_metrics["submit_ok"] = metrics["submit"]["ok"] - sub_ok_before
    leg_metrics["submit_err"] = metrics["submit"]["errors"] - sub_err_before
    print(f"  Submit: {leg_metrics['submit_ok']} OK, {leg_metrics['submit_err']} errors ({submit_ms:.0f}ms)")

    await asyncio.sleep(0.3)

    # ── Re-fetch cards ──
    print(f"  Re-fetching cards...")
    for email in tokens:
        user_cards[email] = await get_game_cards(client, cfg, event_id, tokens[email])

    submitted_pairs = find_pairs_for_leg(user_cards, leg_number, "submitted")
    print(f"  Submitted pairs ready for validation: {len(submitted_pairs)}")

    if submitted_pairs:
        # ── VALIDATE ALL AT ONCE — max concurrent update_standings + recalculate_ranks ──
        print(f"  Validating ALL {len(submitted_pairs)} pairs simultaneously (max pressure)...")
        validate_start = time.monotonic()
        val_ok_before = metrics["validate"]["ok"]
        val_err_before = metrics["validate"]["errors"]

        tasks = []
        for i, (ea, ca, eb, cb) in enumerate(submitted_pairs):
            idx = i + 1
            tasks.append(validate_card(client, cfg, event_id, cb["id"], tokens[ea], f"L{leg_number}V{idx}A->B", metrics))
            tasks.append(validate_card(client, cfg, event_id, ca["id"], tokens[eb], f"L{leg_number}V{idx}B->A", metrics))

        await asyncio.gather(*tasks, return_exceptions=True)

        validate_ms = (time.monotonic() - validate_start) * 1000
        leg_metrics["validate_ok"] = metrics["validate"]["ok"] - val_ok_before
        leg_metrics["validate_err"] = metrics["validate"]["errors"] - val_err_before
        print(f"  Validate: {leg_metrics['validate_ok']} OK, {leg_metrics['validate_err']} errors ({validate_ms:.0f}ms)")

    # Re-fetch for next leg
    for email in tokens:
        user_cards[email] = await get_game_cards(client, cfg, event_id, tokens[email])

    return leg_metrics


# ── Correctness Verification ────────────────────────────────────


async def verify_correctness(client: httpx.AsyncClient, cfg: Config, admin_token: str,
                             event_id: int, num_enrolled: int) -> list[dict]:
    """Run post-test correctness checks. Returns list of {name, passed, detail}."""
    print(f"\n{'═' * 58}")
    print(f"  CORRECTNESS VERIFICATION")
    print(f"{'═' * 58}")

    h = auth(admin_token)
    checks = []

    # Fetch matches
    resp = await client.get(f"{cfg.api_base}/ta/events/{event_id}/matches", headers=h)
    matches = resp.json().get("items", []) if resp.status_code == 200 else []

    # Fetch standings
    resp = await client.get(f"{cfg.api_base}/ta/events/{event_id}/standings", headers=h)
    standings = resp.json().get("items", []) if resp.status_code == 200 else []

    # ── CHECK 1: all_matches_completed ──
    qualifier_matches = [m for m in matches if m.get("phase", "qualifier") == "qualifier"]
    completed = [m for m in qualifier_matches if m["status"] == "completed"]
    c1_pass = len(completed) == len(qualifier_matches) and len(qualifier_matches) > 0
    checks.append({
        "name": "all_matches_completed",
        "passed": c1_pass,
        "detail": f"{len(completed)}/{len(qualifier_matches)} completed" if qualifier_matches else "No matches found",
    })

    # ── Fetch game cards per user (for checks 2 and 8) ──
    all_cards = []
    user_ids_from_standings = [s["user_id"] for s in standings]
    for uid in user_ids_from_standings:
        resp = await client.get(
            f"{cfg.api_base}/ta/events/{event_id}/game-cards",
            headers=h, params={"user_id": uid},
        )
        if resp.status_code == 200:
            cards = resp.json().get("items", [])
            all_cards.extend(cards)

    # ── CHECK 2: all_cards_validated ──
    stuck_cards = [c for c in all_cards if c["status"] in ("draft", "submitted")]
    c2_pass = len(stuck_cards) == 0 and len(all_cards) > 0
    checks.append({
        "name": "all_cards_validated",
        "passed": c2_pass,
        "detail": f"All {len(all_cards)} validated" if c2_pass else f"{len(stuck_cards)} stuck ({', '.join(set(c['status'] for c in stuck_cards))})",
    })

    # ── CHECK 3: standings_count ──
    c3_pass = len(standings) == num_enrolled
    checks.append({
        "name": "standings_count",
        "passed": c3_pass,
        "detail": f"{len(standings)} standings, expected {num_enrolled}",
    })

    # ── CHECK 4: wtl_consistency ──
    wtl_bad = []
    for s in standings:
        w = s.get("victories", 0)
        t = s.get("ties", 0)
        l = s.get("losses", 0)
        mp = s.get("matches_played", 0)
        if w + t + l != mp:
            wtl_bad.append(s.get("user_id"))
    c4_pass = len(wtl_bad) == 0 and len(standings) > 0
    checks.append({
        "name": "wtl_consistency",
        "passed": c4_pass,
        "detail": "All consistent" if c4_pass else f"{len(wtl_bad)} inconsistent: {wtl_bad[:5]}",
    })

    # ── CHECK 5: ranks_sequential ──
    non_dq = [s for s in standings if s.get("rank") is not None]
    ranks = sorted(s["rank"] for s in non_dq)
    expected_ranks = list(range(1, len(non_dq) + 1))
    c5_pass = ranks == expected_ranks and len(non_dq) > 0
    detail5 = f"Ranks 1..{len(non_dq)} OK" if c5_pass else f"Expected {expected_ranks[:5]}... got {ranks[:5]}..."
    checks.append({
        "name": "ranks_sequential",
        "passed": c5_pass,
        "detail": detail5,
    })

    # ── CHECK 6: points_non_negative ──
    neg_pts = [s for s in standings if (s.get("total_points") or 0) < 0]
    c6_pass = len(neg_pts) == 0
    checks.append({
        "name": "points_non_negative",
        "passed": c6_pass,
        "detail": "All non-negative" if c6_pass else f"{len(neg_pts)} negative",
    })

    # ── CHECK 7: match_count_range ──
    # round_robin_half: each user plays ~(N-1)/2 matches per leg × num_legs
    # Allow ±2 margin for odd-number rounding
    if standings:
        n = num_enrolled
        matches_per_leg = (n - 1) // 2
        expected_min = max(1, (matches_per_leg - 1) * cfg.num_legs)
        expected_max = (matches_per_leg + 1) * cfg.num_legs
        out_of_range = [
            s for s in standings
            if not (expected_min <= (s.get("matches_played") or 0) <= expected_max)
        ]
        c7_pass = len(out_of_range) == 0
        checks.append({
            "name": "match_count_range",
            "passed": c7_pass,
            "detail": f"All in range [{expected_min}..{expected_max}]" if c7_pass
            else f"{len(out_of_range)} out of range [{expected_min}..{expected_max}]",
        })
    else:
        checks.append({"name": "match_count_range", "passed": False, "detail": "No standings"})

    # ── CHECK 8: no_partial_writes ──
    # A completed match should not have non-validated cards
    completed_match_ids = {m["id"] for m in qualifier_matches if m["status"] == "completed"}
    partial_write_matches = set()
    for c in all_cards:
        mid = c.get("match_id")
        if mid in completed_match_ids and c["status"] not in ("validated",):
            partial_write_matches.add(mid)
    c8_pass = len(partial_write_matches) == 0
    checks.append({
        "name": "no_partial_writes",
        "passed": c8_pass,
        "detail": "No partial writes detected" if c8_pass
        else f"{len(partial_write_matches)} matches with non-validated cards",
    })

    # Print results
    all_passed = all(c["passed"] for c in checks)
    for c in checks:
        tag = "PASS" if c["passed"] else "FAIL"
        print(f"  [{tag}] {c['name']:<28s} {c['detail']}")
    print()
    if all_passed:
        print(f"  OVERALL: ALL CHECKS PASSED")
    else:
        failed = [c["name"] for c in checks if not c["passed"]]
        print(f"  OVERALL: {len(failed)} CHECK(S) FAILED — {', '.join(failed)}")
    print(f"{'═' * 58}")

    return checks


# ── Summary ─────────────────────────────────────────────────────


def print_summary_table(cfg: Config, metrics: dict, verification_checks: list[dict] | None):
    total_ok = metrics["submit"]["ok"] + metrics["validate"]["ok"]
    total_err = metrics["submit"]["errors"] + metrics["validate"]["errors"]
    total_exc = metrics["submit"]["exceptions"] + metrics["validate"]["exceptions"]
    total_reqs = total_ok + total_err + total_exc

    submit_stats = latency_stats(metrics["submit"]["latencies_ms"])
    validate_stats = latency_stats(metrics["validate"]["latencies_ms"])

    dl = metrics["deadlocks"]

    print(f"\n{'═' * 58}")
    print(f"  PERFORMANCE")
    print(f"{'═' * 58}")
    print(f"  Mode:     {'max-pressure' if cfg.max_pressure else 'batched'}")
    print(f"  Users:    {cfg.num_users}   Legs: {cfg.num_legs}")
    print(f"  Requests: {total_reqs}  OK: {total_ok}  Errors: {total_err}  Exceptions: {total_exc}")
    print(f"  Deadlock 500s: submit={dl['submit']}  validate={dl['validate']}")

    legs_sorted = sorted(metrics["legs"].keys(), key=int)
    for leg in legs_sorted:
        lm = metrics["legs"][leg]
        print(f"  Leg {leg}: submit={lm.get('submit_ok', 0)}/{lm.get('submit_err', 0)}"
              f"  validate={lm.get('validate_ok', 0)}/{lm.get('validate_err', 0)}")

    if submit_stats:
        print(f"  Submit   p50={submit_stats['p50_ms']}ms  p90={submit_stats['p90_ms']}ms  max={submit_stats['max_ms']}ms")
    if validate_stats:
        print(f"  Validate p50={validate_stats['p50_ms']}ms  p90={validate_stats['p90_ms']}ms  max={validate_stats['max_ms']}ms")

    if verification_checks is not None:
        print(f"{'═' * 58}")
        print(f"  CORRECTNESS VERIFICATION")
        print(f"{'═' * 58}")
        for c in verification_checks:
            tag = "PASS" if c["passed"] else "FAIL"
            print(f"  [{tag}] {c['name']:<28s} {c['detail']}")
        all_passed = all(c["passed"] for c in verification_checks)
        print()
        if all_passed:
            print(f"  OVERALL: ALL CHECKS PASSED")
        else:
            failed = [c["name"] for c in verification_checks if not c["passed"]]
            print(f"  OVERALL: {len(failed)} CHECK(S) FAILED — {', '.join(failed)}")

    print(f"{'═' * 58}")

    if total_err > 0 or total_exc > 0:
        print(f"\n  Error details (first 10):")
        for ed in metrics["error_details"][:10]:
            print(f"    {ed}")

    return {
        "total_requests": total_reqs,
        "total_ok": total_ok,
        "total_errors": total_err,
        "total_exceptions": total_exc,
        "success_rate": round(total_ok / total_reqs * 100, 2) if total_reqs else 0,
    }


# ── Main ────────────────────────────────────────────────────────


async def run_stress_test(client, cfg: Config, event_id, tokens, emails, metrics):
    print("\n" + "=" * 60)
    print(f"Phase 3: Leg-by-Leg Stress Test {'[MAX PRESSURE]' if cfg.max_pressure else ''}")
    print("=" * 60)

    metrics["test_start"] = datetime.now(timezone.utc).isoformat()

    # Fetch all game cards
    print("\nFetching game cards for all users...")
    user_cards = {}
    total_cards = 0
    leg_numbers = set()
    for email, token in tokens.items():
        cards = await get_game_cards(client, cfg, event_id, token)
        user_cards[email] = cards
        total_cards += len(cards)
        for c in cards:
            leg_numbers.add(c["leg_number"])

    legs_sorted = sorted(leg_numbers)
    print(f"  Total cards: {total_cards} across {len(tokens)} users")
    print(f"  Legs: {legs_sorted}")
    metrics["total_cards"] = total_cards

    leg_processor = process_leg_max_pressure if cfg.max_pressure else process_leg

    for leg in legs_sorted:
        print(f"\n  Refreshing tokens before leg {leg}...")
        tokens = await refresh_tokens_parallel(client, cfg, emails)
        print(f"  {len(tokens)} tokens refreshed")

        leg_result = await leg_processor(client, cfg, event_id, tokens, user_cards, leg, metrics)
        metrics["legs"][str(leg)] = leg_result

    metrics["test_end"] = datetime.now(timezone.utc).isoformat()
    return tokens  # Return updated tokens for verification


async def main():
    cfg = parse_args()
    emails = [cfg.user_email_template.format(i) for i in range(1, cfg.num_users + 1)]
    metrics = make_metrics(cfg)

    async with httpx.AsyncClient(timeout=60.0) as client:
        print("=" * 60)
        mode_label = "MAX PRESSURE" if cfg.max_pressure else "batched"
        print(f"TA Stress Test — {cfg.num_users} users, {cfg.num_legs} legs, {mode_label}")
        print("=" * 60)

        print("\nLogging in admin...")
        admin_token = await login(client, cfg, cfg.admin_email, cfg.admin_password)
        if not admin_token:
            print("ERROR: Admin login failed"); sys.exit(1)
        print("  OK")

        # Create event
        event_id = await create_event(client, cfg, admin_token)
        if not event_id:
            sys.exit(1)
        metrics["event_id"] = event_id

        # Enroll users
        enrolled = await enroll_users(client, cfg, admin_token, event_id, emails)
        metrics["enrolled_users"] = enrolled

        # Configure + start
        print("\n" + "=" * 60)
        print("Phase 2: Configure & Start")
        print("=" * 60)
        await configure_and_start(client, cfg, admin_token, event_id)

        # Login test users (parallel)
        print("\nLogging in test users...")
        tokens = await refresh_tokens_parallel(client, cfg, emails)
        print(f"  {len(tokens)}/{len(emails)} logged in")
        metrics["logged_in_users"] = len(tokens)

        if len(tokens) < 2:
            print("ERROR: Need at least 2 users"); sys.exit(1)

        # Run stress test
        tokens = await run_stress_test(client, cfg, event_id, tokens, emails, metrics)

        # Correctness verification
        verification_checks = None
        if not cfg.skip_verify:
            # Re-login admin (token may have expired during long test)
            admin_token = await login(client, cfg, cfg.admin_email, cfg.admin_password)
            if admin_token:
                verification_checks = await verify_correctness(
                    client, cfg, admin_token, event_id, enrolled,
                )
            else:
                print("\n  WARN: Admin re-login failed, skipping verification")

        # Summary
        summary = print_summary_table(cfg, metrics, verification_checks)

        # Save metrics
        metrics["submit"]["latency_stats"] = latency_stats(metrics["submit"]["latencies_ms"])
        metrics["validate"]["latency_stats"] = latency_stats(metrics["validate"]["latencies_ms"])
        metrics["summary"] = summary
        if verification_checks:
            metrics["verification_checks"] = verification_checks

        save_metrics = {**metrics}
        save_metrics["submit"] = {k: v for k, v in metrics["submit"].items() if k != "latencies_ms"}
        save_metrics["validate"] = {k: v for k, v in metrics["validate"].items() if k != "latencies_ms"}
        save_metrics["config"] = {
            "max_pressure": cfg.max_pressure,
            "concurrency_batch": cfg.concurrency_batch,
            "num_users": cfg.num_users,
            "num_legs": cfg.num_legs,
        }

        out_path = Path(__file__).parent / "ta_stress_metrics.json"
        with open(out_path, "w") as f:
            json.dump(save_metrics, f, indent=2, default=str)
        print(f"\nMetrics saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
