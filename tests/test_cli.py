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


if __name__ == "__main__":
    unittest.main()
