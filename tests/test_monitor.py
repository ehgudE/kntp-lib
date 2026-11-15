import unittest
from unittest.mock import patch

from kntp.core import NTPKissOfDeathError, Sample, monitor


class FakeClock:
    """time.monotonic/time.sleep 대체: sleep하면 시계가 그만큼 흐르고, 측정 1회마다 0.1초가 흐른다."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run_monitor(servers, offsets, *, rounds, interval=64.0, on_round=None, clock=None):
    """offsets(server, round) -> offset_ms 또는 예외."""
    clock = clock or FakeClock()
    state = {"round": -1, "seen": set()}

    def fake_query(server, timeout):
        if server in state["seen"] or not state["seen"]:
            state["round"] += 1
            state["seen"] = set()
        state["seen"].add(server)
        clock.now += 0.1
        value = offsets(server, state["round"])
        if isinstance(value, Exception):
            raise value
        return Sample(offset_ms=value, delay_ms=10.0)

    with patch("kntp.core.query_ntp", side_effect=fake_query), \
            patch("kntp.core.time.monotonic", clock.monotonic), \
            patch("kntp.core.time.sleep", clock.sleep):
        return monitor(servers, rounds=rounds, interval=interval, on_round=on_round), clock


class MonitorTests(unittest.TestCase):
    def test_rounds_keep_fixed_cadence(self):
        _, clock = run_monitor(["a", "b"], lambda s, r: 0.0, rounds=3, interval=64.0)
        # 각 회차 측정에 0.2초가 걸리므로 다음 회차까지 63.8초만 기다린다.
        self.assertEqual(len(clock.sleeps), 2)
        for wait in clock.sleeps:
            self.assertAlmostEqual(wait, 63.8)

    def test_common_clock_drift_is_not_counted_as_jitter(self):
        # 로컬 시계가 회차마다 5ms씩 흘러가서 모든 서버 offset이 같이 커진다.
        # e만 자체적으로 ±2ms 흔들린다.
        def offsets(server, r):
            noisy = 10.0 + (2.0 if r % 2 else -2.0)
            own = {"a": 0.0, "b": 1.0, "c": 2.0, "d": 3.0, "e": noisy}[server]
            return r * 5.0 + own

        stats, _ = run_monitor(["a", "b", "c", "d", "e"], offsets, rounds=10)
        by = {s.server: s for s in stats}
        for server in "abcd":
            self.assertLess(by[server].std_offset_ms, 0.01, server)
        self.assertAlmostEqual(by["e"].std_offset_ms, 2.0)
        # 평균 offset은 원래 값 그대로다.
        self.assertAlmostEqual(by["a"].avg_offset_ms, 22.5)

    def test_without_enough_servers_uses_raw_std(self):
        stats, _ = run_monitor(["a", "b"], lambda s, r: r * 5.0, rounds=4)
        self.assertGreater(stats[0].std_offset_ms, 5.0)

    def test_on_round_reports_progress_and_partial_stats(self):
        calls = []
        run_monitor(["a", "b"], lambda s, r: 0.0, rounds=3,
                    on_round=lambda done, total, stats: calls.append((done, total, stats[0].ok)))
        self.assertEqual(calls, [(1, 3, 1), (2, 3, 2), (3, 3, 3)])

    def test_failures_and_kiss_of_death(self):
        def offsets(server, r):
            if server == "b":
                return NTPKissOfDeathError("RATE")
            return OSError("timeout") if r == 1 else 0.0

        stats, _ = run_monitor(["a", "b", "c"], offsets, rounds=3)
        by = {s.server: s for s in stats}
        self.assertNotIn("b", by)
        self.assertEqual((by["a"].ok, by["a"].fail), (2, 1))

    def test_validation(self):
        for kwargs in (
            {"rounds": 0},
            {"rounds": True},
            {"rounds": 2.0},
            {"rounds": 3, "interval": 10.0},
            {"rounds": 3, "interval": 7200.0},
            {"rounds": 3, "timeout": 0},
        ):
            with self.assertRaises(ValueError, msg=kwargs):
                monitor(["a.example"], **kwargs)
        with self.assertRaises(ValueError):
            monitor("a.example", rounds=3)


if __name__ == "__main__":
    unittest.main()
