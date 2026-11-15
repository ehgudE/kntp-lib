import struct
import unittest
from unittest.mock import patch

from kntp.core import (
    NTP_ERA_SECONDS,
    NTPKissOfDeathError,
    NTPResponseError,
    Ranked,
    Sample,
    Stats,
    _ntp_to_system,
    _validate_ntp_response,
    collect_stats,
    format_ranked_table,
    query_ntp,
    grade,
    rank_servers,
    recommend,
    recommend_set,
)


def make_response(*, version=4, stratum=1, req_sec=1, req_frac=2, receive=(3, 4), transmit=(5, 6)):
    data = bytearray(48)
    data[0] = (version << 3) | 4
    data[1] = stratum
    struct.pack_into("!II", data, 24, req_sec, req_frac)
    struct.pack_into("!II", data, 32, *receive)
    struct.pack_into("!II", data, 40, *transmit)
    return bytes(data)


def hosts(n):
    return [f"s{i}.example" for i in range(n)]


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
            collect_stats(hosts(65), samples=1)
        with self.assertRaises(ValueError):
            collect_stats(hosts(25), samples=10)
        with self.assertRaises(ValueError):
            collect_stats(hosts(12), samples=21)
        with self.assertRaises(ValueError):
            rank_servers([Stats("base", 1, 0, 0, 0, 1, 0)], base="base", offset_method="median")
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

    def test_default_servers_allow_twenty_samples(self):
        from kntp.core import DEFAULT_SERVERS

        with patch("kntp.core.query_ntp", return_value=Sample(0, 1)) as mock_query:
            stats = collect_stats(DEFAULT_SERVERS, samples=20, sleep_between=0)
        self.assertEqual(mock_query.call_count, len(DEFAULT_SERVERS) * 20)
        self.assertEqual(len(stats), len(DEFAULT_SERVERS))

    def test_probe_limit_error_suggests_max_samples(self):
        with self.assertRaisesRegex(ValueError, r"samples <= 20 for 12 servers"):
            collect_stats(hosts(12), samples=21)

    @patch(
        "kntp.core.query_ntp",
        side_effect=[Sample(10.0, 50.0), Sample(1.0, 5.0), Sample(4.0, 20.0)],
    )
    def test_collect_stats_records_min_delay_offset(self, _mock_query):
        stats = collect_stats(["s1"], samples=3, sleep_between=0)
        self.assertEqual(stats[0].avg_offset_ms, 5.0)
        self.assertEqual(stats[0].min_delay_offset_ms, 1.0)

    def test_rank_servers_min_delay_offset_method(self):
        stats = [
            Stats("base", 5, 0, 0.0, 0.0, 10, 0, min_delay_offset_ms=0.0),
            # 평균은 혼잡 샘플 때문에 크게 벗어나지만, 최소 delay 샘플은 기준과 거의 일치
            Stats("noisy", 5, 0, 8.0, 0.0, 10, 0, min_delay_offset_ms=0.5),
        ]
        by_mean = rank_servers(stats, base="base", w_delay=0, w_jitter=0)
        by_min = rank_servers(stats, base="base", w_delay=0, w_jitter=0, offset_method="min_delay")
        noisy_mean = next(item for item in by_mean if item.server == "noisy")
        noisy_min = next(item for item in by_min if item.server == "noisy")
        self.assertEqual(noisy_mean.vs_base_ms, 8.0)
        self.assertEqual(noisy_min.vs_base_ms, 0.5)

    def test_rank_servers_min_delay_falls_back_to_mean(self):
        stats = [Stats("base", 5, 0, 1.0, 0, 10, 0), Stats("other", 5, 0, 3.0, 0, 10, 0)]
        ranked = rank_servers(stats, base="base", offset_method="min_delay")
        other = next(item for item in ranked if item.server == "other")
        self.assertEqual(other.vs_base_ms, 2.0)

    @patch("kntp.core.query_ntp", side_effect=RuntimeError("unexpected"))
    def test_collect_stats_does_not_swallow_unexpected_errors(self, _mock_query):
        with self.assertRaises(RuntimeError):
            collect_stats(["s1"], samples=1, sleep_between=0)

    def test_format_ranked_table(self):
        ranked = [Ranked("a", 3, 0, 0, 0, 5, 0, 0.1, 1.2, "A")]
        self.assertIn("a", format_ranked_table(ranked))

    # --- 리뷰에서 찾은 버그 회귀 테스트 ---

    def test_unencodable_host_counts_as_failure_not_abort(self):
        def fake_query(host, timeout):
            if host == "bad":
                raise UnicodeEncodeError("idna", host, 0, 1, "label too long")
            return Sample(0, 1)

        with patch("kntp.core.query_ntp", side_effect=fake_query):
            stats = collect_stats(["good", "bad"], samples=2, sleep_between=0)
        self.assertEqual([(item.server, item.ok) for item in stats], [("good", 2)])

    def test_collect_stats_rejects_single_string(self):
        with self.assertRaises(ValueError):
            collect_stats("ntp.kriss.re.kr", samples=1)
        with self.assertRaises(ValueError):
            collect_stats(["ok", ""], samples=1)

    def test_collect_stats_deduplicates_servers(self):
        with patch("kntp.core.query_ntp", return_value=Sample(0, 1)) as mock_query:
            stats = collect_stats(["s", "s", "t"], samples=2, sleep_between=0)
        self.assertEqual([(item.server, item.ok) for item in stats], [("s", 2), ("t", 2)])
        self.assertEqual(mock_query.call_count, 4)

    def test_recommend_skips_custom_base_without_base_argument(self):
        stats = [Stats("my-base", 5, 0, 0, 0, 1, 0), Stats("other", 5, 0, 30, 0, 1, 0)]
        best = recommend(rank_servers(stats, base="my-base"))
        self.assertEqual(best.server, "other")

    def test_recommend_can_pick_kriss_when_it_is_not_the_base(self):
        stats = [Stats("my-base", 5, 0, 0, 0, 1, 0), Stats("ntp.kriss.re.kr", 5, 0, 0.1, 0, 1, 0)]
        best = recommend(rank_servers(stats, base="my-base"))
        self.assertEqual(best.server, "ntp.kriss.re.kr")

    def test_rank_servers_rejects_unreliable_base(self):
        stats = [Stats("base", 1, 6, 50, 0, 10, 0), Stats("good", 7, 0, 0, 0, 10, 0)]
        with self.assertRaises(RuntimeError):
            rank_servers(stats, base="base")
        ranked = rank_servers(stats, base="base", min_base_ok_rate=0.0)
        self.assertEqual(len(ranked), 2)

    def test_single_sample_server_gets_jitter_penalty(self):
        stats = [
            Stats("base", 5, 0, 0, 0, 10, 0),
            Stats("stable", 5, 0, 0.5, 2.0, 10, 0),
            Stats("one-shot", 1, 0, 0.5, 0.0, 10, 0),
        ]
        order = [item.server for item in rank_servers(stats, base="base")]
        self.assertLess(order.index("stable"), order.index("one-shot"))

    def test_kiss_of_death_rate_stops_further_queries(self):
        with patch("kntp.core.query_ntp", side_effect=NTPKissOfDeathError("RATE")) as mock_query:
            stats = collect_stats(["s"], samples=5, sleep_between=0)
        self.assertEqual(mock_query.call_count, 1)
        self.assertEqual(stats, [])

    def test_kiss_of_death_counts_skipped_rounds_as_failures(self):
        responses = [Sample(0, 1), NTPKissOfDeathError("DENY")]
        with patch("kntp.core.query_ntp", side_effect=responses):
            stats = collect_stats(["s"], samples=4, sleep_between=0)
        self.assertEqual((stats[0].ok, stats[0].fail), (1, 3))

    def test_validate_reports_kiss_code(self):
        data = bytearray(make_response(stratum=0))
        data[12:16] = b"RATE"
        with self.assertRaises(NTPKissOfDeathError) as ctx:
            _validate_ntp_response(bytes(data), req_sec=1, req_frac=2)
        self.assertEqual(ctx.exception.code, "RATE")

    def test_query_ntp_delay_uses_monotonic_clock(self):
        # 측정 중 벽시계가 10초 뒤로 조정돼도 delay/offset이 튀지 않아야 한다.
        server_time = 1_800_000_000.0

        class FakeSock:
            def send(self, packet):
                self.orig = packet[40:48]

            def recv(self, _size):
                data = bytearray(48)
                data[0] = (4 << 3) | 4
                data[1] = 1
                data[24:32] = self.orig
                ntp = server_time + 2_208_988_800
                sec, frac = int(ntp), int((ntp % 1) * NTP_ERA_SECONDS)
                struct.pack_into("!IIII", data, 32, sec, frac, sec, frac)
                return bytes(data)

            def close(self):
                pass

        wall = iter([server_time - 0.010, server_time - 10.0])
        mono = iter([100.0, 100.020])
        with patch("kntp.core._connect_udp", return_value=FakeSock()),              patch("kntp.core.time.time", side_effect=lambda: next(wall)),              patch("kntp.core.time.perf_counter", side_effect=lambda: next(mono)):
            sample = query_ntp("fake", timeout=1.0)
        self.assertAlmostEqual(sample.delay_ms, 20.0, places=3)
        self.assertAlmostEqual(sample.offset_ms, 0.0, places=3)

    def test_format_ranked_table_fits_long_names(self):
        ranked = [
            Ranked("very-long-hostname.example.co.kr", 3, 0, 0, 0, 5, 0, 0.1, 1.2, "A"),
            Ranked("short", 3, 0, 0, 0, 5, 0, 0.1, 1.2, "A"),
        ]
        lines = format_ranked_table(ranked).splitlines()
        self.assertEqual(len({len(line) for line in lines}), 1)



def make_stats(server, *, ok=5, fail=0, offset=0.0, delay=10.0, min_delay=None):
    return Stats(
        server=server,
        ok=ok,
        fail=fail,
        avg_offset_ms=offset,
        std_offset_ms=0.0,
        avg_delay_ms=delay,
        std_delay_ms=0.0,
        min_delay_offset_ms=offset,
        min_delay_ms=delay if min_delay is None else min_delay,
    )


class UncertaintyAndSetTests(unittest.TestCase):
    def test_uncertainty_is_half_of_both_delays(self):
        stats = [make_stats("base", delay=4.0), make_stats("a", offset=1.0, delay=10.0)]
        ranked = {r.server: r for r in rank_servers(stats, base="base")}
        self.assertAlmostEqual(ranked["a"].uncertainty_ms, 10.0 / 2 + 4.0 / 2)
        self.assertEqual(ranked["base"].uncertainty_ms, 0.0)

    def test_uncertainty_uses_min_delay_with_min_delay_method(self):
        stats = [
            make_stats("base", delay=4.0, min_delay=2.0),
            make_stats("a", delay=10.0, min_delay=6.0),
        ]
        ranked = {r.server: r for r in rank_servers(stats, base="base", offset_method="min_delay")}
        self.assertAlmostEqual(ranked["a"].uncertainty_ms, 6.0 / 2 + 2.0 / 2)

    def test_collect_stats_records_min_delay(self):
        samples = iter([Sample(offset_ms=5.0, delay_ms=9.0), Sample(offset_ms=1.0, delay_ms=3.0)])
        with patch("kntp.core.query_ntp", side_effect=lambda *a, **k: next(samples)):
            [stat] = collect_stats(["a.example"], samples=2, sleep_between=0)
        self.assertEqual(stat.min_delay_ms, 3.0)
        self.assertEqual(stat.min_delay_offset_ms, 1.0)

    def test_recommend_set_returns_top_servers_without_base(self):
        stats = [make_stats("base", delay=1.0)] + [
            make_stats(name, offset=float(i)) for i, name in enumerate("abcd", start=1)
        ]
        ranked = rank_servers(stats, base="base")
        self.assertEqual([r.server for r in recommend_set(ranked, count=3)], ["a", "b", "c"])

    def test_recommend_set_skips_low_ok_rate_and_may_return_fewer(self):
        stats = [make_stats("base"), make_stats("a", ok=1, fail=4), make_stats("b", offset=5.0)]
        ranked = rank_servers(stats, base="base")
        self.assertEqual([r.server for r in recommend_set(ranked, count=3)], ["b"])

    def test_recommend_set_validates_count(self):
        for bad in (0, 11, True, 2.5):
            with self.assertRaises(ValueError):
                recommend_set([], count=bad)

    def test_table_shows_uncertainty_column(self):
        stats = [make_stats("base"), make_stats("a", offset=1.0)]
        table = format_ranked_table(rank_servers(stats, base="base"))
        self.assertIn("±(ms)", table)
        self.assertIn("10.00", table)


if __name__ == "__main__":
    unittest.main()
