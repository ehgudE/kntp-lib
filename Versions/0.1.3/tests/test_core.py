import struct
import unittest
from unittest.mock import patch

from kntp.core import (
    NTP_ERA_SECONDS,
    NTPResponseError,
    Ranked,
    Sample,
    Stats,
    _ntp_to_system,
    _validate_ntp_response,
    collect_stats,
    format_ranked_table,
    grade,
    rank_servers,
    recommend,
)


def make_response(*, version=4, stratum=1, req_sec=1, req_frac=2, receive=(3, 4), transmit=(5, 6)):
    data = bytearray(48)
    data[0] = (version << 3) | 4
    data[1] = stratum
    struct.pack_into("!II", data, 24, req_sec, req_frac)
    struct.pack_into("!II", data, 32, *receive)
    struct.pack_into("!II", data, 40, *transmit)
    return bytes(data)


class CoreTests(unittest.TestCase):
    def test_grade_boundaries(self):
        self.assertEqual(grade(5), "A")
        self.assertEqual(grade(10), "B")
        self.assertEqual(grade(20), "C")
        self.assertEqual(grade(20.1), "D")

    def test_validate_ntp_response_accepts_valid_packet(self):
        _validate_ntp_response(make_response(), req_sec=1, req_frac=2)

    def test_validate_ntp_response_rejects_unsupported_version(self):
        with self.assertRaises(NTPResponseError):
            _validate_ntp_response(make_response(version=0), req_sec=1, req_frac=2)

    def test_validate_ntp_response_rejects_unsynchronized_stratum(self):
        with self.assertRaises(NTPResponseError):
            _validate_ntp_response(make_response(stratum=16), req_sec=1, req_frac=2)

    def test_validate_ntp_response_rejects_zero_server_timestamps(self):
        with self.assertRaises(NTPResponseError):
            _validate_ntp_response(make_response(receive=(0, 0)), req_sec=1, req_frac=2)
        with self.assertRaises(NTPResponseError):
            _validate_ntp_response(make_response(transmit=(0, 0)), req_sec=1, req_frac=2)

    def test_validate_ntp_response_rejects_mismatched_request(self):
        with self.assertRaises(NTPResponseError):
            _validate_ntp_response(make_response(), req_sec=9, req_frac=9)

    def test_ntp_era_unfolding_stays_near_reference(self):
        reference = 2_200_000_000.0
        wrapped = (reference + 2_208_988_800) % NTP_ERA_SECONDS
        converted = _ntp_to_system(wrapped, reference_unix=reference)
        self.assertAlmostEqual(converted, reference, places=5)

    def test_rank_servers_sort_and_filter(self):
        stats = [
            Stats("base", 5, 0, 0.0, 0.2, 10, 1),
            Stats("fast", 5, 0, 1.0, 0.1, 5, 1),
            Stats("slow", 5, 0, 0.5, 0.1, 200, 1),
        ]
        ranked = rank_servers(stats, base="base", max_delay_ms=100.0)
        self.assertEqual([item.server for item in ranked], ["fast", "base"])

    def test_rank_servers_missing_base_raises(self):
        with self.assertRaises(RuntimeError):
            rank_servers([], base="missing")

    def test_recommend_uses_ok_rate(self):
        ranked = [
            Ranked("base", 5, 0, 0, 0, 1, 0, 0, 0, "A"),
            Ranked("low-ok", 1, 4, 1, 0, 1, 0, 1, 1, "A"),
            Ranked("good", 4, 1, 2, 0, 1, 0, 2, 2, "A"),
        ]
        best = recommend(ranked, base="base", require_ok_rate=0.8)
        self.assertIsNotNone(best)
        self.assertEqual(best.server, "good")

    def test_argument_validation(self):
        with self.assertRaises(ValueError):
            collect_stats(["a"], samples=0)
        with self.assertRaises(ValueError):
            collect_stats(["a"], timeout=0)
        with self.assertRaises(ValueError):
            collect_stats(["a"], sleep_between=-0.1)
        with self.assertRaises(ValueError):
            collect_stats([], samples=1)
        with self.assertRaises(ValueError):
            collect_stats(["a"] * 65, samples=1)
        with self.assertRaises(ValueError):
            collect_stats(["a"] * 11, samples=10)
        with self.assertRaises(ValueError):
            collect_stats(["a"], samples=51)
        with self.assertRaises(ValueError):
            collect_stats(["a"], timeout=10.1)
        with self.assertRaises(ValueError):
            collect_stats(["a"], sleep_between=5.1)
        with self.assertRaises(ValueError):
            recommend([], require_ok_rate=1.1)
        with self.assertRaises(ValueError):
            format_ranked_table([], top_n=0)

    @patch("kntp.core.query_ntp", side_effect=[Sample(1, 2), NTPResponseError("bad")])
    def test_collect_stats_counts_failures(self, _mock_query):
        stats = collect_stats(["s1"], samples=2, sleep_between=0)
        self.assertEqual(stats[0].ok, 1)
        self.assertEqual(stats[0].fail, 1)

    @patch("kntp.core.query_ntp", side_effect=RuntimeError("unexpected"))
    def test_collect_stats_does_not_swallow_unexpected_errors(self, _mock_query):
        with self.assertRaises(RuntimeError):
            collect_stats(["s1"], samples=1, sleep_between=0)

    def test_format_ranked_table(self):
        ranked = [Ranked("a", 3, 0, 0, 0, 5, 0, 0.1, 1.2, "A")]
        self.assertIn("a", format_ranked_table(ranked))


if __name__ == "__main__":
    unittest.main()
