import sys
import unittest
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
