"""
Performance Round 3 — Stress Test for TA + SF endpoint caching.

Simulates live event polling patterns:
- 20 users hitting cached TA endpoints concurrently (rankings, standings,
  game-cards/my, my-matches, statistics, bracket, status)
- 20 users hitting SF leaderboard concurrently
- 5 rounds of concurrent polling per endpoint group
- Background spectator polling (public endpoints, no auth)

Measures latency percentiles, error rates, and cache effectiveness.

Usage:
    python3 scripts/test_perf3_stress.py
    python3 scripts/test_perf3_stress.py --num-users 30 --rounds 10
    python3 scripts/test_perf3_stress.py --ta-event 172 --sf-event 119
"""

import argparse
import asyncio
import base64
import json
import os
import random
import statistics as stats_mod
import time
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx


# ── Config ──────────────────────────────────────────────────────

@dataclass
class Config:
    api_base: str = os.environ.get("REELIN_API_BASE", "https://www.reelin.ro/api/v1")
    admin_email: str = os.environ.get("REELIN_ADMIN_EMAIL", "admin@reelin.ro")
    admin_password: str = os.environ.get("REELIN_ADMIN_PASSWORD", "")
    num_users: int = 20
    user_email_template: str = os.environ.get("REELIN_USER_EMAIL_TPL", "user{}@reelin.ro")
    user_password: str = os.environ.get("REELIN_USER_PASSWORD", "")
    ta_event: int = 0  # auto-detect if 0
    sf_event: int = 0  # auto-detect if 0
    rounds: int = 5
    spectator_count: int = 10  # unauthenticated spectator requests per round
    skip_verify: bool = False


def parse_args() -> Config:
    p = argparse.ArgumentParser(description="Perf Round 3 stress test")
    p.add_argument("--api-base", default=Config.api_base)
    p.add_argument("--num-users", type=int, default=Config.num_users)
    p.add_argument("--ta-event", type=int, default=0)
    p.add_argument("--sf-event", type=int, default=0)
    p.add_argument("--rounds", type=int, default=Config.rounds)
    p.add_argument("--spectator-count", type=int, default=Config.spectator_count)
    p.add_argument("--skip-verify", action="store_true")
    args = p.parse_args()
    return Config(
        api_base=args.api_base,
        num_users=args.num_users,
        ta_event=args.ta_event,
        sf_event=args.sf_event,
        rounds=args.rounds,
        spectator_count=args.spectator_count,
        skip_verify=args.skip_verify,
    )


# ── Helpers ──────────────────────────────────────────────────────

def uid_from_jwt(token: str) -> int:
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return int(json.loads(base64.b64decode(payload))["sub"])


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Metrics:
    name: str
    latencies: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    status_counts: dict = field(default_factory=dict)

    def record(self, elapsed_ms: float, status: int, ok_codes=(200,)):
        self.latencies.append(elapsed_ms)
        self.status_counts[status] = self.status_counts.get(status, 0) + 1
        if status not in ok_codes:
            self.errors.append(status)

    def summary(self) -> dict:
        if not self.latencies:
            return {"name": self.name, "n": 0}
        s = sorted(self.latencies)
        n = len(s)
        return {
            "name": self.name,
            "n": n,
            "avg": round(stats_mod.mean(s), 1),
            "p50": round(s[n // 2], 1),
            "p90": round(s[int(n * 0.9)], 1),
            "p95": round(s[int(n * 0.95)], 1),
            "p99": round(s[min(int(n * 0.99), n - 1)], 1),
            "max": round(s[-1], 1),
            "errors": len(self.errors),
            "status_counts": dict(self.status_counts),
        }

    def print_summary(self):
        s = self.summary()
        if s["n"] == 0:
            print(f"  {s['name']:40s}  NO DATA")
            return
        err_str = f"  ERRORS={s['errors']}" if s["errors"] else ""
        print(
            f"  {s['name']:40s}  n={s['n']:3d}  "
            f"avg={s['avg']:6.0f}  p50={s['p50']:5.0f}  p90={s['p90']:5.0f}  "
            f"p95={s['p95']:5.0f}  p99={s['p99']:5.0f}  max={s['max']:5.0f}{err_str}"
        )


async def timed_request(client, method, url, headers=None, json_data=None):
    t0 = time.monotonic()
    try:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        else:
            resp = await client.post(url, headers=headers, json=json_data)
        elapsed = (time.monotonic() - t0) * 1000
        return resp.status_code, elapsed
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        return 0, elapsed


async def login(client, email, password):
    try:
        resp = await client.post(f"{cfg.api_base}/auth/login", json={
            "email": email, "password": password,
        })
        if resp.status_code != 200:
            return None
        token = resp.json()["access_token"]
        return token, uid_from_jwt(token)
    except Exception:
        return None


async def find_events(client, admin_token):
    resp = await client.get(f"{cfg.api_base}/events?page_size=50", headers=auth(admin_token))
    if resp.status_code != 200:
        return None, None
    ta_id = sf_id = None
    for ev in resp.json().get("items", []):
        et = ev.get("event_type", {})
        code = et.get("code", "") if isinstance(et, dict) else ""
        st = ev.get("status", "")
        if code == "trout_area" and ta_id is None and st in ("ongoing", "completed"):
            ta_id = ev["id"]
        elif code == "street_fishing" and sf_id is None and st in ("ongoing", "completed"):
            sf_id = ev["id"]
        if ta_id and sf_id:
            break
    return ta_id, sf_id


# ── Stress Tests ──────────────────────────────────────────────────

async def stress_ta_authenticated(client, users, event_id, rounds, metrics_map):
    """Simulate authenticated users polling TA endpoints concurrently."""
    endpoints = [
        ("rankings", f"/ta/events/{event_id}/rankings"),
        ("statistics", f"/ta/events/{event_id}/statistics"),
        ("my_matches", f"/ta/events/{event_id}/my-matches"),
        ("game_cards_my", f"/ta/events/{event_id}/game-cards/my"),
    ]

    for ep_key, ep_path in endpoints:
        if ep_key not in metrics_map:
            metrics_map[ep_key] = Metrics(f"TA {ep_key}")

    for round_num in range(rounds):
        # All users hit all endpoints concurrently
        tasks = []
        for token, uid in users:
            for ep_key, ep_path in endpoints:
                tasks.append((ep_key, timed_request(
                    client, "GET", f"{cfg.api_base}{ep_path}", auth(token)
                )))

        # Fire all at once
        coros = [t[1] for t in tasks]
        results = await asyncio.gather(*coros)

        for i, (status, elapsed) in enumerate(results):
            ep_key = tasks[i][0]
            ok = (200, 404) if ep_key == "game_cards_my" else (200,)
            metrics_map[ep_key].record(elapsed, status, ok_codes=ok)

        # Brief pause between rounds to simulate polling interval
        if round_num < rounds - 1:
            await asyncio.sleep(0.5)


async def stress_ta_public(client, event_id, rounds, spectator_count, metrics_map):
    """Simulate spectators polling public TA endpoints."""
    endpoints = [
        ("public_standings", f"/ta/public/events/{event_id}/standings"),
        ("public_status", f"/ta/public/events/{event_id}/status"),
        ("public_bracket", f"/ta/public/events/{event_id}/bracket"),
    ]

    for ep_key, _ in endpoints:
        if ep_key not in metrics_map:
            metrics_map[ep_key] = Metrics(f"TA {ep_key}")

    for round_num in range(rounds):
        tasks = []
        for _ in range(spectator_count):
            for ep_key, ep_path in endpoints:
                tasks.append((ep_key, timed_request(
                    client, "GET", f"{cfg.api_base}{ep_path}"
                )))

        coros = [t[1] for t in tasks]
        results = await asyncio.gather(*coros)

        for i, (status, elapsed) in enumerate(results):
            ep_key = tasks[i][0]
            metrics_map[ep_key].record(elapsed, status, ok_codes=(200, 404))

        if round_num < rounds - 1:
            await asyncio.sleep(0.5)


async def stress_sf_leaderboard(client, event_id, rounds, spectator_count, metrics_map):
    """Simulate spectators polling SF leaderboard."""
    m = Metrics("SF leaderboard")
    metrics_map["sf_leaderboard"] = m

    for round_num in range(rounds):
        tasks = []
        for _ in range(spectator_count):
            tasks.append(timed_request(
                client, "GET", f"{cfg.api_base}/catches/leaderboard/{event_id}"
            ))

        results = await asyncio.gather(*tasks)
        for status, elapsed in results:
            m.record(elapsed, status)

        if round_num < rounds - 1:
            await asyncio.sleep(0.5)


async def stress_auth_switched(client, users, rounds, metrics_map):
    """Stress test Part A auth-switched endpoints."""
    endpoints = [
        ("notifications", "/notifications"),
        ("notifications_stats", "/notifications/stats"),
        ("achievements_me", "/achievements/me"),
        ("rules", "/rules"),
        ("rules_defaults", "/rules/defaults"),
        ("follows_me", "/users/me/following"),
    ]

    for ep_key, _ in endpoints:
        if ep_key not in metrics_map:
            metrics_map[ep_key] = Metrics(f"Auth {ep_key}")

    for round_num in range(rounds):
        tasks = []
        for token, uid in users:
            ep_key, ep_path = random.choice(endpoints)
            tasks.append((ep_key, timed_request(
                client, "GET", f"{cfg.api_base}{ep_path}", auth(token)
            )))

        coros = [t[1] for t in tasks]
        results = await asyncio.gather(*coros)

        for i, (status, elapsed) in enumerate(results):
            ep_key = tasks[i][0]
            metrics_map[ep_key].record(elapsed, status)

        if round_num < rounds - 1:
            await asyncio.sleep(0.3)


async def mixed_load_test(client, users, ta_event, sf_event, rounds, spectator_count, metrics_map):
    """
    Simulate realistic mixed load: authenticated users + public spectators
    all polling simultaneously, like a live event.
    """
    print(f"  Firing {rounds} rounds of mixed load...")

    for round_num in range(rounds):
        tasks = []
        task_keys = []

        # Authenticated users poll TA endpoints
        if ta_event:
            for token, uid in users:
                # Each user polls 2-3 random TA endpoints
                ep_choices = [
                    ("mixed_rankings", f"/ta/events/{ta_event}/rankings"),
                    ("mixed_my_matches", f"/ta/events/{ta_event}/my-matches"),
                    ("mixed_game_cards", f"/ta/events/{ta_event}/game-cards/my"),
                    ("mixed_statistics", f"/ta/events/{ta_event}/statistics"),
                ]
                selected = random.sample(ep_choices, min(3, len(ep_choices)))
                for ep_key, ep_path in selected:
                    tasks.append(timed_request(client, "GET", f"{cfg.api_base}{ep_path}", auth(token)))
                    task_keys.append(ep_key)

        # Public spectators poll standings + status + leaderboard
        for _ in range(spectator_count):
            if ta_event:
                tasks.append(timed_request(client, "GET", f"{cfg.api_base}/ta/public/events/{ta_event}/standings"))
                task_keys.append("mixed_pub_standings")
                tasks.append(timed_request(client, "GET", f"{cfg.api_base}/ta/public/events/{ta_event}/status"))
                task_keys.append("mixed_pub_status")
            if sf_event:
                tasks.append(timed_request(client, "GET", f"{cfg.api_base}/catches/leaderboard/{sf_event}"))
                task_keys.append("mixed_sf_leaderboard")

        # Fire everything at once
        results = await asyncio.gather(*tasks)

        for i, (status, elapsed) in enumerate(results):
            key = task_keys[i]
            if key not in metrics_map:
                metrics_map[key] = Metrics(f"Mixed {key.replace('mixed_', '')}")
            metrics_map[key].record(elapsed, status, ok_codes=(200, 404))

        sys.stdout.write(f"\r  Round {round_num + 1}/{rounds} done ({len(tasks)} requests)")
        sys.stdout.flush()

        if round_num < rounds - 1:
            await asyncio.sleep(1.0)  # simulate polling interval

    print()


# ── Main ──────────────────────────────────────────────────────────

cfg: Config = Config()


async def main():
    global cfg
    cfg = parse_args()

    print("=" * 75)
    print("Performance Round 3 — Stress Test")
    print(f"Users: {cfg.num_users} | Rounds: {cfg.rounds} | Spectators: {cfg.spectator_count}")
    print("=" * 75)

    timeout = httpx.Timeout(60.0, connect=10.0)
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        # ── Login ──
        print("\n[1/6] Authenticating...")
        admin_result = await login(client, cfg.admin_email, cfg.admin_password)
        if not admin_result:
            print("FATAL: Admin login failed")
            return
        admin_token, _ = admin_result
        print(f"  Admin OK")

        users = []
        login_tasks = [
            login(client, cfg.user_email_template.format(i), cfg.user_password)
            for i in range(1, cfg.num_users + 1)
        ]
        login_results = await asyncio.gather(*login_tasks)
        for r in login_results:
            if r:
                users.append(r)
        print(f"  {len(users)}/{cfg.num_users} users logged in")

        if len(users) < 3:
            print("FATAL: Not enough users")
            return

        # ── Find events ──
        print("\n[2/6] Finding events...")
        ta_event = cfg.ta_event or None
        sf_event = cfg.sf_event or None
        if not ta_event or not sf_event:
            ta_auto, sf_auto = await find_events(client, admin_token)
            ta_event = ta_event or ta_auto
            sf_event = sf_event or sf_auto
        print(f"  TA event: {ta_event or 'NOT FOUND'}")
        print(f"  SF event: {sf_event or 'NOT FOUND'}")

        # ── Phase 1: Isolated endpoint stress ──
        print(f"\n[3/6] Isolated TA stress ({len(users)} users x {cfg.rounds} rounds)...")
        ta_metrics = {}
        if ta_event:
            await stress_ta_authenticated(client, users, ta_event, cfg.rounds, ta_metrics)
            await stress_ta_public(client, ta_event, cfg.rounds, cfg.spectator_count, ta_metrics)
            for m in ta_metrics.values():
                m.print_summary()
        else:
            print("  SKIP: no TA event")

        print(f"\n[4/6] Isolated SF stress ({cfg.spectator_count} spectators x {cfg.rounds} rounds)...")
        sf_metrics = {}
        if sf_event:
            await stress_sf_leaderboard(client, sf_event, cfg.rounds, cfg.spectator_count, sf_metrics)
            for m in sf_metrics.values():
                m.print_summary()
        else:
            print("  SKIP: no SF event")

        # ── Phase 2: Auth-switched endpoints ──
        print(f"\n[5/6] Auth-switched endpoints stress ({len(users)} users x {cfg.rounds} rounds)...")
        auth_metrics = {}
        await stress_auth_switched(client, users, cfg.rounds, auth_metrics)
        for m in auth_metrics.values():
            m.print_summary()

        # ── Phase 3: Mixed load (realistic) ──
        print(f"\n[6/6] Mixed load test (all endpoints simultaneously)...")
        mixed_metrics = {}
        await mixed_load_test(
            client, users, ta_event, sf_event,
            cfg.rounds, cfg.spectator_count, mixed_metrics
        )
        for m in mixed_metrics.values():
            m.print_summary()

        # ── Save metrics ──
        all_metrics = {}
        for d in [ta_metrics, sf_metrics, auth_metrics, mixed_metrics]:
            for k, m in d.items():
                all_metrics[k] = m.summary()

        total_requests = sum(m["n"] for m in all_metrics.values())
        total_errors = sum(m.get("errors", 0) for m in all_metrics.values())
        all_latencies = []
        for d in [ta_metrics, sf_metrics, auth_metrics, mixed_metrics]:
            for m in d.values():
                all_latencies.extend(m.latencies)

        output = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "config": {
                "num_users": cfg.num_users,
                "rounds": cfg.rounds,
                "spectator_count": cfg.spectator_count,
                "ta_event": ta_event,
                "sf_event": sf_event,
            },
            "summary": {
                "total_requests": total_requests,
                "total_errors": total_errors,
                "error_rate_pct": round(total_errors / total_requests * 100, 2) if total_requests else 0,
                "global_avg_ms": round(stats_mod.mean(all_latencies), 1) if all_latencies else 0,
                "global_p50_ms": round(sorted(all_latencies)[len(all_latencies) // 2], 1) if all_latencies else 0,
                "global_p95_ms": round(sorted(all_latencies)[int(len(all_latencies) * 0.95)], 1) if all_latencies else 0,
                "global_p99_ms": round(sorted(all_latencies)[min(int(len(all_latencies) * 0.99), len(all_latencies) - 1)], 1) if all_latencies else 0,
            },
            "endpoints": all_metrics,
        }

        metrics_path = Path(__file__).parent / "perf3_stress_metrics.json"
        metrics_path.write_text(json.dumps(output, indent=2))

        print(f"\n{'=' * 75}")
        print(f"RESULTS SUMMARY")
        print(f"{'=' * 75}")
        print(f"  Total requests:  {total_requests}")
        print(f"  Total errors:    {total_errors} ({output['summary']['error_rate_pct']}%)")
        print(f"  Global avg:      {output['summary']['global_avg_ms']:.0f}ms")
        print(f"  Global p50:      {output['summary']['global_p50_ms']:.0f}ms")
        print(f"  Global p95:      {output['summary']['global_p95_ms']:.0f}ms")
        print(f"  Global p99:      {output['summary']['global_p99_ms']:.0f}ms")
        print(f"\n  Metrics saved to: {metrics_path}")
        print(f"{'=' * 75}")


if __name__ == "__main__":
    asyncio.run(main())
