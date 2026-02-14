import unittest
from unittest.mock import patch

from kntp.core import (
    NTPResponseError,
    Ranked,
    Sample,
    Stats,
    _validate_ntp_response,
    collect_stats,
    format_ranked_table,
    grade,
    rank_servers,
    recommend,
)


class CoreTests(unittest.TestCase):
    def test_grade_boundaries(self):
        self.assertEqual(grade(5), "A")
        self.assertEqual(grade(10), "B")
        self.assertEqual(grade(20), "C")
        self.assertEqual(grade(20.1), "D")

    def test_rank_servers_sort_and_filter(self):
        stats = [
            Stats("base", ok=5, fail=0, avg_offset_ms=0.0, std_offset_ms=0.2, avg_delay_ms=10, std_delay_ms=1),
            Stats("fast", ok=5, fail=0, avg_offset_ms=1.0, std_offset_ms=0.1, avg_delay_ms=5, std_delay_ms=1),
            Stats("slow", ok=5, fail=0, avg_offset_ms=0.5, std_offset_ms=0.1, avg_delay_ms=200, std_delay_ms=1),
        ]
        ranked = rank_servers(stats, base="base", max_delay_ms=100.0)

        self.assertEqual([r.server for r in ranked], ["fast", "base"])

    def test_rank_servers_missing_base_raises(self):
        with self.assertRaises(RuntimeError):
            rank_servers([], base="missing")

    def test_recommend_uses_ok_rate(self):
        ranked = [
            Ranked("base", ok=5, fail=0, avg_offset_ms=0, std_offset_ms=0, avg_delay_ms=1, std_delay_ms=0, vs_base_ms=0, score=0, grade="A"),
            Ranked("low-ok", ok=1, fail=4, avg_offset_ms=1, std_offset_ms=0, avg_delay_ms=1, std_delay_ms=0, vs_base_ms=1, score=1, grade="A"),
            Ranked("good", ok=4, fail=1, avg_offset_ms=2, std_offset_ms=0, avg_delay_ms=1, std_delay_ms=0, vs_base_ms=2, score=2, grade="A"),
        ]

        best = recommend(ranked, base="base", require_ok_rate=0.8)
        self.assertIsNotNone(best)
        self.assertEqual(best.server, "good")

    def test_collect_stats_validates_samples(self):
        with self.assertRaises(ValueError):
            collect_stats(["a"], samples=0)


    def test_collect_stats_validates_timing_arguments(self):
        with self.assertRaises(ValueError):
            collect_stats(["a"], samples=1, timeout=0)
        with self.assertRaises(ValueError):
            collect_stats(["a"], samples=1, sleep_between=-0.1)

    def test_rank_servers_validates_weight_and_delay_arguments(self):
        stats = [
            Stats("base", ok=1, fail=0, avg_offset_ms=0.0, std_offset_ms=0.0, avg_delay_ms=10, std_delay_ms=0.0),
        ]
        with self.assertRaises(ValueError):
            rank_servers(stats, base="base", w_delay=-0.1)
        with self.assertRaises(ValueError):
            rank_servers(stats, base="base", w_jitter=-0.1)
        with self.assertRaises(ValueError):
            rank_servers(stats, base="base", max_delay_ms=0)

    def test_recommend_validates_ok_rate_range(self):
        with self.assertRaises(ValueError):
            recommend([], require_ok_rate=-0.1)
        with self.assertRaises(ValueError):
            recommend([], require_ok_rate=1.1)


    def test_format_ranked_table(self):
        ranked = [
            Ranked("a", ok=3, fail=0, avg_offset_ms=0, std_offset_ms=0, avg_delay_ms=5, std_delay_ms=0, vs_base_ms=0.1, score=1.2, grade="A"),
            Ranked("b", ok=2, fail=1, avg_offset_ms=0, std_offset_ms=0, avg_delay_ms=9, std_delay_ms=0, vs_base_ms=-0.2, score=2.3, grade="B"),
        ]
        rendered = format_ranked_table(ranked, top_n=1)
        self.assertIn("rank", rendered)
        self.assertIn("a", rendered)
        self.assertNotIn("\n2    b", rendered)

    def test_format_ranked_table_validates_top_n(self):
        with self.assertRaises(ValueError):
            format_ranked_table([], top_n=0)

    @patch("kntp.core.query_ntp", side_effect=[Sample(1, 2), NTPResponseError("bad")])
    def test_collect_stats_counts_failures(self, _mock_query):
        stats = collect_stats(["s1"], samples=2, sleep_between=0)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].ok, 1)
        self.assertEqual(stats[0].fail, 1)

    @patch("kntp.core.query_ntp", side_effect=RuntimeError("unexpected"))
    def test_collect_stats_does_not_swallow_unexpected_errors(self, _mock_query):
        with self.assertRaises(RuntimeError):
            collect_stats(["s1"], samples=1, sleep_between=0)

    def test_validate_ntp_response_mode_check(self):
        data = bytearray(48)
        data[0] = 0x23  # mode=3, invalid for server response
        data[1] = 1
        with self.assertRaises(NTPResponseError):
            _validate_ntp_response(bytes(data), req_sec=1, req_frac=2)


if __name__ == "__main__":
    unittest.main()
