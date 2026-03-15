"""
Perf Round 3 — Full E2E stress test with writes + reads.

TA flow: fetch game cards -> submit catches -> validate opponent -> poll standings/rankings
SF flow: upload unique fish images concurrently + validate concurrently + poll leaderboard

Usage:
    export REELIN_ADMIN_PASSWORD=xxx REELIN_USER_PASSWORD=xxx
    python3 scripts/test_perf3_e2e.py --ta-event 300 --sf-event 301
    python3 scripts/test_perf3_e2e.py --ta-event 300 --sf-event 301 --ta-legs 3 --sf-catches 8
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

# Fish images — 6686 unique images across species subdirs
FISH_IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "ml" / "data" / "fish_images"
DUMMY_PHOTO_URL = "https://reelin-uploads.ams3.cdn.digitaloceanspaces.com/test/stress_test_catch.jpg"

# Pre-scan all images once
_ALL_IMAGES: list[Path] = []


def _scan_images():
    global _ALL_IMAGES
    if _ALL_IMAGES:
        return
    if FISH_IMAGES_DIR.exists():
        for ext in ("*.jpg", "*.jpeg", "*.png"):
            _ALL_IMAGES.extend(FISH_IMAGES_DIR.rglob(ext))
        random.shuffle(_ALL_IMAGES)
    print(f"  Fish images found: {len(_ALL_IMAGES)} in {FISH_IMAGES_DIR}")


_image_idx = 0


def next_fish_image() -> tuple[bytes, str, str]:
    """Get next unique fish image (round-robin through 6686 images)."""
    global _image_idx
    if not _ALL_IMAGES:
        return None, None, None
    img = _ALL_IMAGES[_image_idx % len(_ALL_IMAGES)]
    _image_idx += 1
    content_type = "image/jpeg" if img.suffix in (".jpg", ".jpeg") else "image/png"
    return img.read_bytes(), img.name, content_type


@dataclass
class Config:
    api_base: str = os.environ.get("REELIN_API_BASE", "https://www.reelin.ro/api/v1")
    admin_email: str = os.environ.get("REELIN_ADMIN_EMAIL", "admin@reelin.ro")
    admin_password: str = os.environ.get("REELIN_ADMIN_PASSWORD", "")
    user_email_template: str = os.environ.get("REELIN_USER_EMAIL_TPL", "user{}@reelin.ro")
    user_password: str = os.environ.get("REELIN_USER_PASSWORD", "")
    ta_event: int = 0
    sf_event: int = 0
    ta_legs: int = 3
    sf_catches: int = 5  # catches per user per round
    num_users: int = 20
    wave_size: int = 10  # concurrent requests per wave


def parse_args() -> Config:
    p = argparse.ArgumentParser(description="Perf Round 3 E2E stress test")
    p.add_argument("--ta-event", type=int, default=0, required=True)
    p.add_argument("--sf-event", type=int, default=0, required=True)
    p.add_argument("--ta-legs", type=int, default=3)
    p.add_argument("--sf-catches", type=int, default=5, help="catches per user")
    p.add_argument("--num-users", type=int, default=20)
    p.add_argument("--wave-size", type=int, default=10)
    args = p.parse_args()
    return Config(
        ta_event=args.ta_event, sf_event=args.sf_event,
        ta_legs=args.ta_legs, sf_catches=args.sf_catches,
        num_users=args.num_users, wave_size=args.wave_size,
    )


# ── Helpers ────────────────────────────────────────────────────

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
    ok: int = 0
    errors: int = 0
    error_details: list = field(default_factory=list)

    def record(self, elapsed_ms: float, status: int, ok_codes=(200, 201)):
        self.latencies.append(elapsed_ms)
        if status in ok_codes:
            self.ok += 1
        else:
            self.errors += 1

    def print_line(self):
        if not self.latencies:
            print(f"  {self.name:42s} NO DATA")
            return
        s = sorted(self.latencies)
        n = len(s)
        p50 = s[n // 2]
        p95 = s[int(n * 0.95)]
        avg = stats_mod.mean(s)
        err = f" ERRORS={self.errors}" if self.errors else ""
        print(f"  {self.name:42s} n={n:4d}  avg={avg:6.0f}  p50={p50:5.0f}  p95={p95:5.0f}  max={s[-1]:5.0f}{err}")


async def timed_get(client, url, headers=None):
    t0 = time.monotonic()
    try:
        resp = await client.get(url, headers=headers)
        return resp.status_code, (time.monotonic() - t0) * 1000, resp
    except Exception:
        return 0, (time.monotonic() - t0) * 1000, None


async def timed_post(client, url, headers=None, json_data=None):
    t0 = time.monotonic()
    try:
        resp = await client.post(url, headers=headers, json=json_data)
        return resp.status_code, (time.monotonic() - t0) * 1000, resp
    except Exception:
        return 0, (time.monotonic() - t0) * 1000, None


async def login_all(client, cfg):
    resp = await client.post(f"{cfg.api_base}/auth/login", json={
        "email": cfg.admin_email, "password": cfg.admin_password,
    })
    if resp.status_code != 200:
        print(f"FATAL: Admin login failed: {resp.status_code}")
        return None, []
    admin_token = resp.json()["access_token"]

    users = []
    tasks = [
        client.post(f"{cfg.api_base}/auth/login", json={
            "email": cfg.user_email_template.format(i), "password": cfg.user_password,
        })
        for i in range(1, cfg.num_users + 1)
    ]
    results = await asyncio.gather(*tasks)
    for resp in results:
        if resp.status_code == 200:
            token = resp.json()["access_token"]
            users.append((token, uid_from_jwt(token)))
    return admin_token, users


# ── TA Flow ────────────────────────────────────────────────────

async def fetch_game_cards(client, cfg, event_id, token):
    code, ms, resp = await timed_get(
        client, f"{cfg.api_base}/ta/events/{event_id}/game-cards/my", auth(token)
    )
    if code == 200:
        return resp.json().get("items", [])
    return []


async def ta_submit_card(client, cfg, event_id, card_id, catches, token, metrics):
    code, ms, resp = await timed_post(
        client, f"{cfg.api_base}/ta/events/{event_id}/game-cards/{card_id}/submit",
        auth(token), {"my_catches": catches}
    )
    metrics.record(ms, code)
    if code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", "")[:120] if resp else ""
        except Exception:
            pass
        metrics.error_details.append(f"submit card={card_id}: {code} {detail}")
    return code == 200


async def ta_validate_card(client, cfg, event_id, card_id, token, metrics):
    code, ms, resp = await timed_post(
        client, f"{cfg.api_base}/ta/events/{event_id}/game-cards/{card_id}/validate",
        auth(token), {"is_valid": True}
    )
    metrics.record(ms, code)
    if code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", "")[:120] if resp else ""
        except Exception:
            pass
        metrics.error_details.append(f"validate card={card_id}: {code} {detail}")
    return code == 200


async def run_ta_leg(client, cfg, event_id, leg_num, users, user_map,
                     metrics_submit, metrics_validate, metrics_reads):
    # Fetch all game cards
    fetch_tasks = [fetch_game_cards(client, cfg, event_id, tok) for tok, uid in users]
    results = await asyncio.gather(*fetch_tasks)
    cards_by_user = {}
    for (tok, uid), cards in zip(users, results):
        cards_by_user[uid] = cards

    # Find draft pairs
    pairs = []
    matched = set()
    for uid, cards in cards_by_user.items():
        for card in cards:
            if card.get("leg_number") == leg_num and card.get("status") == "draft" and card["id"] not in matched:
                opp_id = card.get("opponent_id")
                if opp_id and opp_id in cards_by_user:
                    for opp_card in cards_by_user[opp_id]:
                        if opp_card.get("leg_number") == leg_num and opp_card.get("status") == "draft" and opp_card["id"] not in matched:
                            tok_a = user_map.get(uid)
                            tok_b = user_map.get(opp_id)
                            if tok_a and tok_b:
                                pairs.append((card, opp_card, tok_a, tok_b))
                                matched.add(card["id"])
                                matched.add(opp_card["id"])
                            break

    print(f"    {len(pairs)} pairs")
    if not pairs:
        return

    # Submit all cards concurrently
    submit_tasks = []
    for card_a, card_b, tok_a, tok_b in pairs:
        submit_tasks.append(ta_submit_card(client, cfg, event_id, card_a["id"], random.randint(0, 12), tok_a, metrics_submit))
        submit_tasks.append(ta_submit_card(client, cfg, event_id, card_b["id"], random.randint(0, 12), tok_b, metrics_submit))
    await asyncio.gather(*submit_tasks)

    # Concurrent read polling between submit and validate
    poll_tasks = []
    for tok, uid in random.sample(users, min(10, len(users))):
        poll_tasks.append(timed_get(client, f"{cfg.api_base}/ta/events/{event_id}/rankings", auth(tok)))
        poll_tasks.append(timed_get(client, f"{cfg.api_base}/ta/public/events/{event_id}/standings"))
        poll_tasks.append(timed_get(client, f"{cfg.api_base}/ta/events/{event_id}/my-matches", auth(tok)))
    poll_results = await asyncio.gather(*poll_tasks)
    for code, ms, _ in poll_results:
        metrics_reads.record(ms, code, ok_codes=(200, 404))

    await asyncio.sleep(0.3)

    # Validate all cards concurrently (cross-validation: B validates A, A validates B)
    validate_tasks = []
    for card_a, card_b, tok_a, tok_b in pairs:
        validate_tasks.append(ta_validate_card(client, cfg, event_id, card_a["id"], tok_b, metrics_validate))
        validate_tasks.append(ta_validate_card(client, cfg, event_id, card_b["id"], tok_a, metrics_validate))
    await asyncio.gather(*validate_tasks)

    # Post-validate read polling
    poll_tasks = []
    for tok, uid in random.sample(users, min(10, len(users))):
        poll_tasks.append(timed_get(client, f"{cfg.api_base}/ta/events/{event_id}/rankings", auth(tok)))
        poll_tasks.append(timed_get(client, f"{cfg.api_base}/ta/events/{event_id}/statistics", auth(tok)))
        poll_tasks.append(timed_get(client, f"{cfg.api_base}/ta/public/events/{event_id}/standings"))
    poll_results = await asyncio.gather(*poll_tasks)
    for code, ms, _ in poll_results:
        metrics_reads.record(ms, code, ok_codes=(200, 404))


# ── SF Flow ────────────────────────────────────────────────────

async def sf_submit_catch(client, cfg, event_id, fish_id, token, metrics):
    """Submit catch with unique image upload."""
    length = round(random.uniform(15.0, 60.0), 1)
    img_bytes, img_name, img_type = next_fish_image()

    t0 = time.monotonic()
    try:
        if img_bytes:
            resp = await client.post(
                f"{cfg.api_base}/catches/upload",
                headers=auth(token),
                params={"event_id": event_id, "fish_id": fish_id, "length": length},
                files={"photo": (img_name, img_bytes, img_type)},
            )
        else:
            resp = await client.post(
                f"{cfg.api_base}/catches",
                headers=auth(token),
                json={"event_id": event_id, "fish_id": fish_id, "length": length, "photo_url": DUMMY_PHOTO_URL},
            )
        ms = (time.monotonic() - t0) * 1000
        metrics.record(ms, resp.status_code)
        if resp.status_code in (200, 201):
            return resp.json()
        else:
            detail = ""
            try:
                detail = resp.json().get("detail", "")[:120]
            except Exception:
                pass
            metrics.error_details.append(f"submit: {resp.status_code} {detail}")
            return None
    except Exception as e:
        ms = (time.monotonic() - t0) * 1000
        metrics.record(ms, 0)
        metrics.error_details.append(f"submit: EXCEPTION {e}")
        return None


async def sf_validate_catch(client, cfg, catch_id, token, metrics):
    code, ms, resp = await timed_post(
        client, f"{cfg.api_base}/catches/{catch_id}/validate",
        auth(token), {"status": "approved"}
    )
    metrics.record(ms, code)
    if code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", "")[:120] if resp else ""
        except Exception:
            pass
        metrics.error_details.append(f"validate catch={catch_id}: {code} {detail}")
    return code == 200


async def run_sf_round(client, cfg, event_id, users, admin_token, fish_ids,
                       round_num, m_submit, m_validate, m_reads, pending_catches):
    """
    Submit catches from all users concurrently + poll leaderboard simultaneously.
    Then validate submitted catches concurrently + poll leaderboard simultaneously.
    """
    # ── Submit + Poll simultaneously ──
    submit_tasks = []
    poll_tasks = []

    # All users submit a catch concurrently
    for tok, uid in users:
        fish_id = random.choice(fish_ids)
        submit_tasks.append(sf_submit_catch(client, cfg, event_id, fish_id, tok, m_submit))

    # 10 spectators poll leaderboard at the same time
    for _ in range(10):
        poll_tasks.append(timed_get(client, f"{cfg.api_base}/catches/leaderboard/{event_id}"))

    # Fire all at once
    all_results = await asyncio.gather(*submit_tasks, *poll_tasks)

    # Separate results
    submit_results = all_results[:len(submit_tasks)]
    poll_results = all_results[len(submit_tasks):]

    new_catches = [c for c in submit_results if c is not None]
    pending_catches.extend(new_catches)

    for code, ms, _ in poll_results:
        m_reads.record(ms, code)

    # ── Validate pending catches + Poll simultaneously ──
    # Validate in waves of wave_size, with leaderboard polling alongside each wave
    to_validate = list(pending_catches)
    pending_catches.clear()

    if to_validate:
        for wave_start in range(0, len(to_validate), cfg.wave_size):
            wave = to_validate[wave_start:wave_start + cfg.wave_size]
            validate_tasks = []
            poll_tasks_v = []

            for catch_data in wave:
                catch_id = catch_data.get("id")
                if catch_id:
                    validate_tasks.append(sf_validate_catch(client, cfg, catch_id, admin_token, m_validate))

            # Concurrent leaderboard polls during validation
            for _ in range(5):
                poll_tasks_v.append(timed_get(client, f"{cfg.api_base}/catches/leaderboard/{event_id}"))

            all_v = await asyncio.gather(*validate_tasks, *poll_tasks_v)
            for result in all_v[len(validate_tasks):]:
                code, ms, _ = result
                m_reads.record(ms, code)

    return len(new_catches)


# ── Main ────────────────────────────────────────────────────────

async def main():
    cfg = parse_args()
    _scan_images()

    total_sf_uploads = cfg.sf_catches * cfg.num_users
    print("=" * 75)
    print("Perf Round 3 — Full E2E Stress Test (writes + reads)")
    print(f"TA event: {cfg.ta_event} ({cfg.ta_legs} legs, {cfg.num_users} players)")
    print(f"SF event: {cfg.sf_event} ({cfg.sf_catches} rounds x {cfg.num_users} users = {total_sf_uploads} image uploads)")
    print(f"Wave size: {cfg.wave_size}")
    print("=" * 75)

    timeout = httpx.Timeout(120.0, connect=10.0)
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        # Login
        print("\n[1] Authenticating...")
        admin_token, users = await login_all(client, cfg)
        if not admin_token:
            return
        user_map = {uid: tok for tok, uid in users}
        print(f"  Admin + {len(users)} users logged in")

        resp = await client.get(f"{cfg.api_base}/fish?page_size=5", headers=auth(admin_token))
        fish_ids = [f["id"] for f in resp.json()[:3]]
        print(f"  Fish IDs: {fish_ids}")

        all_metrics = {}

        # ── TA E2E ──
        if cfg.ta_event:
            print(f"\n{'='*75}")
            print(f"[2] TA E2E — {cfg.ta_legs} legs: submit + validate + concurrent polling")
            print(f"{'='*75}")
            m_submit = Metrics("TA submit")
            m_validate = Metrics("TA validate")
            m_reads = Metrics("TA concurrent reads (rankings/standings)")
            all_metrics["ta_submit"] = m_submit
            all_metrics["ta_validate"] = m_validate
            all_metrics["ta_reads"] = m_reads

            for leg in range(1, cfg.ta_legs + 1):
                sys.stdout.write(f"  Leg {leg}/{cfg.ta_legs}: ")
                sys.stdout.flush()
                await run_ta_leg(client, cfg, cfg.ta_event, leg, users, user_map,
                                m_submit, m_validate, m_reads)
                print(f"  submit={m_submit.ok}ok/{m_submit.errors}err  validate={m_validate.ok}ok/{m_validate.errors}err")

            print()
            m_submit.print_line()
            m_validate.print_line()
            m_reads.print_line()
            if m_submit.error_details:
                print(f"  Submit errors (first 5): {m_submit.error_details[:5]}")
            if m_validate.error_details:
                print(f"  Validate errors (first 5): {m_validate.error_details[:5]}")

        # ── SF E2E ──
        if cfg.sf_event:
            print(f"\n{'='*75}")
            print(f"[3] SF E2E — {cfg.sf_catches} rounds: {cfg.num_users} concurrent image uploads")
            print(f"    + concurrent validation + concurrent leaderboard polling")
            print(f"{'='*75}")
            m_sf_submit = Metrics("SF submit (image upload)")
            m_sf_validate = Metrics("SF validate")
            m_sf_reads = Metrics("SF leaderboard (concurrent polls)")
            all_metrics["sf_submit"] = m_sf_submit
            all_metrics["sf_validate"] = m_sf_validate
            all_metrics["sf_reads"] = m_sf_reads

            pending_catches = []
            total_uploaded = 0

            for round_num in range(1, cfg.sf_catches + 1):
                n = await run_sf_round(
                    client, cfg, cfg.sf_event, users, admin_token, fish_ids,
                    round_num, m_sf_submit, m_sf_validate, m_sf_reads, pending_catches
                )
                total_uploaded += n
                print(f"  Round {round_num}/{cfg.sf_catches}: +{n} catches uploaded+validated  (total: {total_uploaded})")

            print()
            m_sf_submit.print_line()
            m_sf_validate.print_line()
            m_sf_reads.print_line()
            if m_sf_submit.error_details:
                print(f"  Submit errors (first 5): {m_sf_submit.error_details[:5]}")
            if m_sf_validate.error_details:
                print(f"  Validate errors (first 5): {m_sf_validate.error_details[:5]}")

        # ── Summary ──
        print(f"\n{'=' * 75}")
        print("RESULTS SUMMARY")
        print(f"{'=' * 75}")
        total_n = sum(len(m.latencies) for m in all_metrics.values())
        total_err = sum(m.errors for m in all_metrics.values())
        all_lat = []
        for m in all_metrics.values():
            all_lat.extend(m.latencies)
        if all_lat:
            s = sorted(all_lat)
            print(f"  Total requests:  {total_n}")
            print(f"  Total errors:    {total_err} ({total_err / total_n * 100:.1f}%)")
            print(f"  Global avg:      {stats_mod.mean(s):.0f}ms")
            print(f"  Global p50:      {s[len(s) // 2]:.0f}ms")
            print(f"  Global p95:      {s[int(len(s) * 0.95)]:.0f}ms")
            print(f"  Global p99:      {s[min(int(len(s) * 0.99), len(s) - 1)]:.0f}ms")

        # Save metrics
        output = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "config": {"ta_event": cfg.ta_event, "sf_event": cfg.sf_event,
                             "ta_legs": cfg.ta_legs, "sf_catches": cfg.sf_catches,
                             "num_users": cfg.num_users, "wave_size": cfg.wave_size},
                  "endpoints": {}}
        for k, m in all_metrics.items():
            if m.latencies:
                s = sorted(m.latencies)
                output["endpoints"][k] = {
                    "n": len(s), "ok": m.ok, "errors": m.errors,
                    "avg": round(stats_mod.mean(s), 1),
                    "p50": round(s[len(s) // 2], 1),
                    "p95": round(s[int(len(s) * 0.95)], 1),
                    "p99": round(s[min(int(len(s) * 0.99), len(s) - 1)], 1),
                    "max": round(s[-1], 1),
                }
        metrics_path = Path(__file__).parent / "perf3_e2e_metrics.json"
        metrics_path.write_text(json.dumps(output, indent=2))
        print(f"\n  Metrics saved to: {metrics_path}")
        print(f"{'=' * 75}")


if __name__ == "__main__":
    asyncio.run(main())
