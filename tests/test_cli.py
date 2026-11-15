import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from kntp import cli
from kntp.core import Stats


def stat(server, offset=0.0, delay=10.0, ok=5, fail=0):
    return Stats(server, ok, fail, offset, 0.0, delay, 0.0, offset, delay)


FAKE = [
    stat("ntp.kriss.re.kr", delay=4.0),
    stat("time.bora.net", offset=1.0),
    stat("kr.pool.ntp.org", offset=2.0),
    stat("time.google.com", offset=3.0, delay=40.0),
]


def run(*argv, stats=FAKE):
    out, err = io.StringIO(), io.StringIO()
    with patch("kntp.cli.collect_stats", return_value=stats) as collect, \
            redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue(), collect


class CliTests(unittest.TestCase):
    def test_table_lists_recommended_secondaries(self):
        code, out, _, _ = run("-c", "2")
        self.assertEqual(code, 0)
        recommended = out.split("추천 보조 서버")[1]
        self.assertIn("1. time.bora.net", recommended)
        self.assertIn("2. kr.pool.ntp.org", recommended)
        self.assertNotIn("3.", recommended)

    def test_chrony_output_uses_pool_directive_and_prefers_base(self):
        code, out, _, _ = run("-f", "chrony")
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(lines[1], "server ntp.kriss.re.kr iburst prefer")
        self.assertIn("server time.bora.net iburst", lines)
        self.assertIn("pool kr.pool.ntp.org iburst", lines)

    def test_w32tm_output(self):
        code, out, _, _ = run("-f", "w32tm", "-c", "1")
        self.assertEqual(code, 0)
        self.assertIn('/manualpeerlist:"ntp.kriss.re.kr,0x8 time.bora.net,0x8"', out)

    def test_json_output(self):
        code, out, _, _ = run("-f", "json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["base"], "ntp.kriss.re.kr")
        self.assertEqual(data["recommended"][0], "time.bora.net")
        self.assertIn("uncertainty_ms", data["ranked"][0])

    def test_base_is_added_to_custom_server_list(self):
        _, _, _, collect = run("time.bora.net")
        self.assertEqual(collect.call_args.args[0], ["ntp.kriss.re.kr", "time.bora.net"])

    def test_rejects_unsafe_server_names(self):
        code, _, _, collect = run('bad"name')
        self.assertEqual(code, 2)
        collect.assert_not_called()

    def test_rejects_bad_count_before_measuring(self):
        code, _, _, collect = run("-c", "0")
        self.assertEqual(code, 2)
        collect.assert_not_called()

    def test_base_failure_returns_1(self):
        code, _, err, _ = run(stats=[stat("time.bora.net")])
        self.assertEqual(code, 1)
        self.assertIn("Base server", err)

    def test_no_recommendation_returns_1(self):
        code, out, _, _ = run(stats=[stat("ntp.kriss.re.kr"), stat("x.example", ok=1, fail=9)])
        self.assertEqual(code, 1)
        self.assertIn("보조 서버가 없습니다", out)



def run_watch(*argv, monitor_impl):
    out, err = io.StringIO(), io.StringIO()
    with patch("kntp.cli.monitor", side_effect=monitor_impl) as mon,             patch("kntp.cli.collect_stats") as collect,             redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue(), mon, collect


class WatchCliTests(unittest.TestCase):
    def test_watch_computes_rounds_from_duration(self):
        def impl(servers, *, rounds, interval, timeout, on_round):
            on_round(1, rounds, FAKE)
            return FAKE

        code, out, err, mon, collect = run_watch("--watch", "1h", monitor_impl=impl)
        self.assertEqual(code, 0)
        collect.assert_not_called()
        self.assertEqual(mon.call_args.kwargs["rounds"], 57)  # ceil(3600 / 64)
        self.assertEqual(mon.call_args.kwargs["interval"], 64.0)
        self.assertIn("장기 측정 시작", err)
        self.assertIn("[1/57]", err)
        self.assertIn("1. time.bora.net", out)

    def test_watch_custom_interval_and_json(self):
        code, out, _, mon, _ = run_watch(
            "-w", "10m", "--interval", "30", "-f", "json",
            monitor_impl=lambda servers, **kw: FAKE,
        )
        self.assertEqual(code, 0)
        self.assertEqual(mon.call_args.kwargs["rounds"], 20)
        data = json.loads(out)
        self.assertEqual((data["mode"], data["rounds"]), ("watch", 20))

    def test_ctrl_c_uses_last_completed_round(self):
        def impl(servers, *, rounds, interval, timeout, on_round):
            on_round(1, rounds, FAKE)
            on_round(2, rounds, FAKE)
            raise KeyboardInterrupt

        code, out, err, _, _ = run_watch("-w", "1h", "-f", "json", monitor_impl=impl)
        self.assertEqual(code, 0)
        self.assertIn("2회차까지", err)
        self.assertEqual(json.loads(out)["rounds"], 2)

    def test_ctrl_c_before_first_round_fails(self):
        def impl(servers, **kw):
            raise KeyboardInterrupt

        code, _, err, _, _ = run_watch("-w", "1h", monitor_impl=impl)
        self.assertEqual(code, 1)
        self.assertIn("완료된 회차 없음", err)

    def test_invalid_watch_and_interval(self):
        code, *_ , mon, _ = run_watch("-w", "soon", monitor_impl=lambda *a, **k: FAKE)
        self.assertEqual(code, 2)
        mon.assert_not_called()
        # 간격 하한(16초)은 monitor()가 검사하고 ValueError -> 종료 코드 2
        code, *_ = run_watch("-w", "1h", "--interval", "5",
                             monitor_impl=__import__("kntp.core", fromlist=["monitor"]).monitor)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
