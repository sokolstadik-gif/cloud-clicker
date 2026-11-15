import unittest
from datetime import date

from new_year_rules import (
    active_gift_boost_count,
    can_activate_gift_boost,
    daily_gift_limit,
    era_power_multiplier,
    era_unlock_progress,
    winter_calendar_status,
    winter_ticket_reward,
    winter_ticket_total,
)


class NewYearRulesTests(unittest.TestCase):
    def test_era_power_is_four_times_the_previous_era(self):
        self.assertEqual([era_power_multiplier(i) for i in range(1, 5)], [1, 4, 16, 64])
        with self.assertRaises(ValueError):
            era_power_multiplier(5)

    def test_ticket_calendar_is_31_days_and_increases_by_two(self):
        self.assertEqual(winter_ticket_reward(1), 10)
        self.assertEqual(winter_ticket_reward(2), 12)
        self.assertEqual(winter_ticket_reward(31), 70)
        self.assertEqual(winter_ticket_total(), 1240)

    def test_ticket_day_bounds(self):
        for day in (0, 32):
            with self.assertRaises(ValueError):
                winter_ticket_reward(day)

    def test_gift_limits_follow_current_era(self):
        self.assertEqual([daily_gift_limit(i) for i in range(1, 5)], [5, 10, 15, 20])

    def test_two_active_boosts_block_a_third_but_expired_slot_is_free(self):
        boosts = [
            {"expires_at": 110, "active": True},
            {"expires_at": 120, "active": True},
            {"expires_at": 90, "active": True},
        ]
        self.assertEqual(active_gift_boost_count(boosts, 100), 2)
        self.assertFalse(can_activate_gift_boost(boosts, 100))
        self.assertTrue(can_activate_gift_boost(boosts, 111))

    def test_era_one_requires_taps_made_in_era_one(self):
        result = era_unlock_progress(1, era1_taps=4999)
        self.assertEqual(result["remaining"], 1)
        self.assertFalse(result["unlocked"])
        self.assertTrue(era_unlock_progress(1, era1_taps=5000)["unlocked"])

    def test_era_three_requires_fifty_purchases_in_era_two(self):
        self.assertFalse(era_unlock_progress(2, era2_upgrade_purchases=49)["unlocked"])
        self.assertTrue(era_unlock_progress(2, era2_upgrade_purchases=50)["unlocked"])

    def test_era_four_sums_earned_in_eras_two_and_three_not_balance(self):
        self.assertEqual(era_unlock_progress(3, era2_earned=600000, era3_earned=399999)["remaining"], 1)
        self.assertTrue(era_unlock_progress(3, era2_earned=600000, era3_earned=400000)["unlocked"])

    def test_calendar_does_not_roll_missed_days_forward(self):
        start = date(2026, 12, 1)
        self.assertEqual(winter_calendar_status(start, date(2026, 12, 4))["day"], 4)
        self.assertFalse(winter_calendar_status(start, date(2026, 11, 30))["active"])
        self.assertTrue(winter_calendar_status(start, date(2027, 1, 1))["finished"])


if __name__ == "__main__":
    unittest.main()
