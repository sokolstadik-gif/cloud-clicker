import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as game


class PostgreSQLIntegrationTest(unittest.TestCase):
    def setUp(self):
        game.app.config.update(TESTING=True)
        self.client = game.app.test_client()
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["ok"])

        with self.client.session_transaction() as session:
            self.csrf = session["csrf_token"]

    def post(self, path, payload=None):
        return self.client.post(
            path,
            json=payload or {},
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_core_game_flow_persists_in_postgresql(self):
        # Claiming the daily reward exercises inserts, updates and a transaction.
        daily = self.post("/api/daily")
        self.assertEqual(daily.status_code, 200, daily.get_data(as_text=True))
        self.assertTrue(daily.get_json()["ok"])

        # Buy an upgrade, then click and confirm that player progress is saved.
        upgrade = self.post("/api/upgrade", {"id": "tap"})
        self.assertEqual(upgrade.status_code, 200, upgrade.get_data(as_text=True))
        self.assertTrue(upgrade.get_json()["ok"])

        click = self.post("/api/click", {"count": 2})
        self.assertEqual(click.status_code, 200, click.get_data(as_text=True))
        self.assertTrue(click.get_json()["ok"])
        self.assertEqual(click.get_json()["accepted_count"], 2)

        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200, state.get_data(as_text=True))
        saved = state.get_json()["state"]
        self.assertEqual(saved["total_taps"], 2)
        self.assertEqual(saved["upgrades"][0]["level"], 1)
        self.assertGreaterEqual(saved["coins"], 0)

        leaderboard = self.client.get("/api/leaderboard")
        self.assertEqual(leaderboard.status_code, 200, leaderboard.get_data(as_text=True))
        self.assertTrue(leaderboard.get_json()["ok"])


    def test_concurrent_gift_activation_respects_two_slot_limit(self):
        receiver_id = uuid.uuid4().hex
        gift_ids = [uuid.uuid4().hex for _ in range(3)]
        with game.db_connection() as conn:
            game.ensure_player(conn, receiver_id)
            for gift_id in gift_ids:
                conn.execute(
                    """INSERT INTO player_gifts
                       (id, sender_id, receiver_id, gift_code, kind, rarity, effect,
                        duration_seconds, created_at, status)
                       VALUES (?, ?, ?, 'warm_glow', 'boost', 'common', 'income_10', 3600, ?, 'inventory')""",
                    (gift_id, receiver_id, receiver_id, game.time.time()),
                )
            conn.execute(
                "UPDATE player_gifts SET status = 'active', activated_at = ?, expires_at = ? WHERE id = ?",
                (game.time.time(), game.time.time() + 3600, gift_ids[0]),
            )

        clients = [game.app.test_client(), game.app.test_client()]
        csrf_tokens = []
        for client in clients:
            with client.session_transaction() as session:
                session["player_id"] = receiver_id
                session["csrf_token"] = uuid.uuid4().hex
                csrf_tokens.append(session["csrf_token"])

        def activate(args):
            client, csrf, gift_id = args
            return client.post(
                "/api/gifts/activate",
                json={"gift_id": gift_id},
                headers={"X-CSRF-Token": csrf},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(activate, [
                (clients[0], csrf_tokens[0], gift_ids[1]),
                (clients[1], csrf_tokens[1], gift_ids[2]),
            ]))

        self.assertEqual(sorted(statuses), [200, 409])
        with game.db_connection() as conn:
            active = conn.execute(
                "SELECT COUNT(*) AS total FROM player_gifts WHERE receiver_id = ? AND status = 'active' AND expires_at > ?",
                (receiver_id, game.time.time()),
            ).fetchone()
        self.assertEqual(int(active["total"]), 2)

    def test_concurrent_successful_payment_webhooks_credit_once(self):
        player_id = uuid.uuid4().hex
        payment_id = uuid.uuid4().hex
        payload = "test-payment-" + uuid.uuid4().hex
        telegram_id = str(8_000_000_000 + uuid.uuid4().int % 1_000_000_000)
        coins = 12_345
        with game.db_connection() as conn:
            game.ensure_player(conn, player_id)
            conn.execute(
                """INSERT INTO star_payments
                   (id, payload, telegram_id, player_id, package_id, stars_amount,
                    coins_amount, status, created_at)
                   VALUES (?, ?, ?, ?, 'integration-test', 25, ?, 'pending', ?)""",
                (payment_id, payload, telegram_id, player_id, coins, game.time.time()),
            )

        message = {
            "from": {"id": int(telegram_id)},
            "successful_payment": {
                "invoice_payload": payload,
                "currency": "XTR",
                "total_amount": 25,
                "telegram_payment_charge_id": "charge-" + uuid.uuid4().hex,
            },
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(game.process_successful_payment, message) for _ in range(2)]
            for future in futures:
                future.result(timeout=30)

        with game.db_connection() as conn:
            player = conn.execute(
                "SELECT coins, total_earned FROM players WHERE id = ?", (player_id,)
            ).fetchone()
            payment = conn.execute(
                "SELECT status FROM star_payments WHERE id = ?", (payment_id,)
            ).fetchone()
        self.assertEqual(int(player["coins"]), coins)
        self.assertEqual(int(player["total_earned"]), coins)
        self.assertEqual(payment["status"], "paid")

    def test_concurrent_gift_sends_respect_daily_limit(self):
        sender_id = uuid.uuid4().hex
        receiver_id = uuid.uuid4().hex
        sender_telegram_id = str(7_000_000_000 + uuid.uuid4().int % 1_000_000_000)
        receiver_telegram_id = str(6_000_000_000 + uuid.uuid4().int % 1_000_000_000)
        recipient_code = "friend-" + uuid.uuid4().hex
        day_start = game.datetime.datetime.now(game.datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        with game.db_connection() as conn:
            game.ensure_player(conn, sender_id)
            game.ensure_player(conn, receiver_id)
            conn.execute(
                """INSERT INTO telegram_accounts
                   (telegram_id, player_id, username, first_name, last_name, created_at, last_seen_at)
                   VALUES (?, ?, '', '', '', ?, ?)""",
                (sender_telegram_id, sender_id, game.time.time(), game.time.time()),
            )
            conn.execute(
                """INSERT INTO telegram_accounts
                   (telegram_id, player_id, username, first_name, last_name, created_at, last_seen_at)
                   VALUES (?, ?, '', '', '', ?, ?)""",
                (receiver_telegram_id, receiver_id, game.time.time(), game.time.time()),
            )
            conn.execute(
                "INSERT INTO referral_codes (player_id, code, created_at) VALUES (?, ?, ?)",
                (receiver_id, recipient_code, game.time.time()),
            )
            for _ in range(4):
                conn.execute(
                    """INSERT INTO player_gifts
                       (id, sender_id, receiver_id, gift_code, kind, rarity, duration_seconds, created_at, status)
                       VALUES (?, ?, ?, 'snowflake_charm', 'decor', 'common', 0, ?, 'collected')""",
                    (uuid.uuid4().hex, sender_id, receiver_id, day_start + 1),
                )

        clients = [game.app.test_client(), game.app.test_client()]
        csrf_tokens = []
        for client in clients:
            response = client.get(
                "/api/state",
                headers={"X-Telegram-Init-Data": sender_telegram_id},
            )
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            with client.session_transaction() as session:
                csrf_tokens.append(session["csrf_token"])

        def fake_validate(raw):
            return {
                "id": int(raw), "username": "", "first_name": "", "last_name": "",
                "start_param": "",
            }

        def send(args):
            client, csrf = args
            return client.post(
                "/api/gifts/send",
                json={"recipient_code": recipient_code, "gift_code": "snowflake_charm"},
                headers={"X-CSRF-Token": csrf, "X-Telegram-Init-Data": sender_telegram_id},
            ).status_code

        with patch.object(game, "validate_telegram_init_data", side_effect=fake_validate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(send, zip(clients, csrf_tokens)))

        self.assertEqual(sorted(statuses), [200, 429])
        with game.db_connection() as conn:
            sent = conn.execute(
                "SELECT COUNT(*) AS total FROM player_gifts WHERE sender_id = ? AND created_at >= ?",
                (sender_id, day_start),
            ).fetchone()
        self.assertEqual(int(sent["total"]), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
