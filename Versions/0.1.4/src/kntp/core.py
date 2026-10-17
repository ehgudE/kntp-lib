"""Core logic for KRISS-based NTP comparison and recommendation."""

from __future__ import annotations

import math
import socket
import struct
import time
from dataclasses import dataclass
from statistics import mean, pstdev

NTP_PORT = 123
NTP_DELTA = 2208988800
NTP_ERA_SECONDS = 2**32

MAX_SERVERS = 64
MAX_SAMPLES = 50
MAX_TIMEOUT = 10.0
MAX_SLEEP_BETWEEN = 5.0
MAX_TOTAL_PROBES = 100

DEFAULT_BASE = "ntp.kriss.re.kr"

DEFAULT_SERVERS: list[str] = [
    "ntp.kriss.re.kr",
    "kr.pool.ntp.org",
    "asia.pool.ntp.org",
    "pool.ntp.org",
    "time.bora.net",
    "time.nuri.net",
    "clock.iptime.co.kr",
    "time.google.com",
    "time.cloudflare.com",
    "time.windows.com",
    "time.apple.com",
    "time.facebook.com",
]


class NTPResponseError(ValueError):
    """Raised when an NTP response is malformed or not trustworthy."""


@dataclass(frozen=True)
class Sample:
    """단일 측정 결과."""

    offset_ms: float
    delay_ms: float


@dataclass(frozen=True)
class Stats:
    """서버별 통계."""

    server: str
    ok: int
    fail: int
    avg_offset_ms: float
    std_offset_ms: float
    avg_delay_ms: float
    std_delay_ms: float


@dataclass(frozen=True)
class Ranked:
    """랭킹/추천용 결과(기준 서버 대비)."""

    server: str
    ok: int
    fail: int
    avg_offset_ms: float
    std_offset_ms: float
    avg_delay_ms: float
    std_delay_ms: float
    vs_base_ms: float
    score: float
    grade: str


def grade(score: float) -> str:
    """점수 기반 등급(A가 가장 좋음)."""
    if score <= 5:
        return "A"
    if score <= 10:
        return "B"
    if score <= 20:
        return "C"
    return "D"


def _system_to_ntp(ts_unix: float) -> float:
    return ts_unix + NTP_DELTA


def _ntp_to_system(ts_ntp: float, *, reference_unix: float) -> float:
    """Convert a 32-bit NTP timestamp using the era nearest reference_unix."""
    reference_ntp = _system_to_ntp(reference_unix)
    era = round((reference_ntp - ts_ntp) / NTP_ERA_SECONDS)
    return ts_ntp + (era * NTP_ERA_SECONDS) - NTP_DELTA


def _validate_ntp_response(data: bytes, req_sec: int, req_frac: int) -> None:
    if len(data) < 48:
        raise NTPResponseError("NTP response too short")

    li_vn_mode = data[0]
    leap = (li_vn_mode >> 6) & 0b11
    version = (li_vn_mode >> 3) & 0b111
    mode = li_vn_mode & 0b111
    stratum = data[1]

    if version not in (3, 4):
        raise NTPResponseError(f"Unsupported NTP version in response: {version}")
    if mode != 4:
        raise NTPResponseError(f"Invalid NTP mode in response: {mode}")
    if leap == 3:
        raise NTPResponseError("NTP server clock unsynchronized (LI=3)")
    if not 1 <= stratum <= 15:
        raise NTPResponseError(f"Invalid or unsynchronized NTP stratum: {stratum}")

    originate_sec, originate_frac = struct.unpack("!II", data[24:32])
    if (originate_sec, originate_frac) != (req_sec, req_frac):
        raise NTPResponseError("NTP originate timestamp mismatch")

    receive_sec, receive_frac = struct.unpack("!II", data[32:40])
    transmit_sec, transmit_frac = struct.unpack("!II", data[40:48])
    if (receive_sec, receive_frac) == (0, 0):
        raise NTPResponseError("NTP receive timestamp is zero")
    if (transmit_sec, transmit_frac) == (0, 0):
        raise NTPResponseError("NTP transmit timestamp is zero")


def _connect_udp(host: str, timeout: float) -> socket.socket:
    """Open a connected IPv4/IPv6 UDP socket so packets only come from the peer."""
    last_error: OSError | None = None
    for family, socktype, proto, _, sockaddr in socket.getaddrinfo(
        host, NTP_PORT, type=socket.SOCK_DGRAM
    ):
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise socket.gaierror(f"No UDP address found for {host}")


def query_ntp(host: str, timeout: float = 2.0) -> Sample:
    """Query one NTP server and compute delay/offset using 4-timestamp equations."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")
    if len(host) > 253:
        raise ValueError("host must be at most 253 characters")
    if not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be > 0 and <= {MAX_TIMEOUT}")

    sock = _connect_udp(host, timeout)
    packet = bytearray(48)
    packet[0] = 0x23

    t1 = time.time()
    t1_ntp = _system_to_ntp(t1)
    req_sec_full = int(t1_ntp)
    req_sec = req_sec_full % NTP_ERA_SECONDS
    req_frac = int((t1_ntp - req_sec_full) * NTP_ERA_SECONDS)
    struct.pack_into("!II", packet, 40, req_sec, req_frac)

    try:
        sock.send(packet)
        data = sock.recv(512)
        t4 = time.time()
    finally:
        sock.close()

    _validate_ntp_response(data, req_sec=req_sec, req_frac=req_frac)
    u = struct.unpack("!12I", data[:48])
    t2_ntp = u[8] + (u[9] / NTP_ERA_SECONDS)
    t3_ntp = u[10] + (u[11] / NTP_ERA_SECONDS)

    t2 = _ntp_to_system(t2_ntp, reference_unix=t1)
    t3 = _ntp_to_system(t3_ntp, reference_unix=t4)
    if t3 < t2:
        raise NTPResponseError("NTP transmit timestamp precedes receive timestamp")

    delay = (t4 - t1) - (t3 - t2)
    offset = ((t2 - t1) + (t3 - t4)) / 2
    if not math.isfinite(delay) or not math.isfinite(offset):
        raise NTPResponseError("NTP calculation produced a non-finite value")
    if delay < -0.001:
        raise NTPResponseError("NTP calculation produced an invalid negative delay")

    return Sample(offset_ms=offset * 1000.0, delay_ms=max(0.0, delay * 1000.0))


def collect_stats(
    servers: list[str],
    samples: int = 5,
    timeout: float = 2.0,
    sleep_between: float = 0.5,
) -> list[Stats]:
    """servers 각 서버를 samples번 측정해서 통계를 반환."""
    if not 1 <= len(servers) <= MAX_SERVERS:
        raise ValueError(f"servers count must be between 1 and {MAX_SERVERS}")
    if not 1 <= samples <= MAX_SAMPLES:
        raise ValueError(f"samples must be between 1 and {MAX_SAMPLES}")
    if len(servers) * samples > MAX_TOTAL_PROBES:
        raise ValueError(f"servers * samples must be <= {MAX_TOTAL_PROBES}")
    if not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be > 0 and <= {MAX_TIMEOUT}")
    if not 0 <= sleep_between <= MAX_SLEEP_BETWEEN:
        raise ValueError(f"sleep_between must be between 0 and {MAX_SLEEP_BETWEEN}")

    raw: dict[str, list[Sample]] = {s: [] for s in servers}
    fails: dict[str, int] = {s: 0 for s in servers}

    for i in range(samples):
        for server in servers:
            try:
                raw[server].append(query_ntp(server, timeout=timeout))
            except (socket.timeout, socket.gaierror, OSError, struct.error, NTPResponseError):
                fails[server] += 1
        if i != samples - 1 and sleep_between > 0:
            time.sleep(sleep_between)

    out: list[Stats] = []
    for server in servers:
        ok = len(raw[server])
        fail = fails[server]
        if ok == 0:
            continue

        offsets = [sample.offset_ms for sample in raw[server]]
        delays = [sample.delay_ms for sample in raw[server]]
        out.append(
            Stats(
                server=server,
                ok=ok,
                fail=fail,
                avg_offset_ms=mean(offsets),
                std_offset_ms=pstdev(offsets) if ok > 1 else 0.0,
                avg_delay_ms=mean(delays),
                std_delay_ms=pstdev(delays) if ok > 1 else 0.0,
            )
        )
    return out


def rank_servers(
    stats: list[Stats],
    base: str = DEFAULT_BASE,
    *,
    w_delay: float = 0.20,
    w_jitter: float = 0.50,
    max_delay_ms: float | None = 100.0,
    allow_base: bool = True,
) -> list[Ranked]:
    """기준(base) 대비 점수화해 정렬한 리스트 반환."""
    if w_delay < 0 or w_jitter < 0:
        raise ValueError("w_delay and w_jitter must be >= 0")
    if max_delay_ms is not None and max_delay_ms <= 0:
        raise ValueError("max_delay_ms must be > 0 when provided")

    base_stat = next((item for item in stats if item.server == base), None)
    if base_stat is None:
        raise RuntimeError(f"Base server '{base}' stats not found (측정 실패/목록 누락).")

    ranked: list[Ranked] = []
    for item in stats:
        if not allow_base and item.server == base:
            continue
        vs_base = item.avg_offset_ms - base_stat.avg_offset_ms
        score = abs(vs_base) + (w_delay * item.avg_delay_ms) + (w_jitter * item.std_offset_ms)
        if max_delay_ms is not None and item.avg_delay_ms >= max_delay_ms:
            continue
        ranked.append(
            Ranked(
                server=item.server,
                ok=item.ok,
                fail=item.fail,
                avg_offset_ms=item.avg_offset_ms,
                std_offset_ms=item.std_offset_ms,
                avg_delay_ms=item.avg_delay_ms,
                std_delay_ms=item.std_delay_ms,
                vs_base_ms=vs_base,
                score=score,
                grade=grade(score),
            )
        )
    ranked.sort(key=lambda item: item.score)
    return ranked


def recommend(
    ranked: list[Ranked],
    *,
    base: str = DEFAULT_BASE,
    require_ok_rate: float = 0.8,
) -> Ranked | None:
    """랭킹 결과에서 성공률 조건을 만족하는 추천 1개를 반환."""
    if not 0.0 <= require_ok_rate <= 1.0:
        raise ValueError("require_ok_rate must be between 0.0 and 1.0")
    for item in ranked:
        if item.server == base:
            continue
        total = item.ok + item.fail
        ok_rate = (item.ok / total) if total > 0 else 0.0
        if ok_rate >= require_ok_rate:
            return item
    return None


def format_ranked_table(ranked: list[Ranked], *, top_n: int | None = 5) -> str:
    """Return a readable text table for ranking results."""
    if top_n is not None and top_n < 1:
        raise ValueError("top_n must be >= 1 when provided")
    rows = ranked[:top_n] if top_n is not None else ranked
    if not rows:
        return "(no ranked results)"

    header = f"{'rank':<4} {'server':<22} {'score':>8} {'grade':>5} {'vs_base(ms)':>12} {'delay(ms)':>10} {'ok/fail':>8}"
    lines = [header, "-" * len(header)]
    for index, item in enumerate(rows, start=1):
        lines.append(
            f"{index:<4} {item.server:<22} {item.score:>8.2f} {item.grade:>5} "
            f"{item.vs_base_ms:>12.2f} {item.avg_delay_ms:>10.2f} {item.ok:>2}/{item.fail:<5}"
        )
    return "\n".join(lines)
