"""Core logic for KRISS-based NTP comparison and recommendation."""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from statistics import mean, pstdev

NTP_PORT = 123
NTP_DELTA = 2208988800  # seconds between 1900-01-01 and 1970-01-01

DEFAULT_BASE = "ntp.kriss.re.kr"

DEFAULT_SERVERS: list[str] = [
    # Korea / KR-centric
    "ntp.kriss.re.kr",     # KRISS (기준)
    "kr.pool.ntp.org",     # Korea NTP Pool
    "asia.pool.ntp.org",   # Asia NTP Pool
    "pool.ntp.org",        # Global NTP Pool
    "time.bora.net",       # 국내에서 흔히 사용
    "time.nuri.net",       # 국내에서 흔히 사용
    "clock.iptime.co.kr",  # 환경에 따라 응답/차단 가능
    # Global public (fallback/비교용)
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
    """단일 측정 결과"""

    offset_ms: float  # clock offset (server vs local) in ms
    delay_ms: float   # network delay in ms


@dataclass(frozen=True)
class Stats:
    """서버별 통계"""

    server: str
    ok: int
    fail: int
    avg_offset_ms: float
    std_offset_ms: float
    avg_delay_ms: float
    std_delay_ms: float


@dataclass(frozen=True)
class Ranked:
    """랭킹/추천용 결과(기준 서버 대비)"""

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
    """점수 기반 등급(A가 가장 좋음). 필요하면 사용자 환경에 맞게 조정."""
    if score <= 5:
        return "A"
    if score <= 10:
        return "B"
    if score <= 20:
        return "C"
    return "D"


def _system_to_ntp(ts_unix: float) -> float:
    return ts_unix + NTP_DELTA


def _ntp_to_system(ts_ntp: float) -> float:
    return ts_ntp - NTP_DELTA


def _validate_ntp_response(data: bytes, req_sec: int, req_frac: int) -> None:
    if len(data) < 48:
        raise NTPResponseError("NTP response too short")

    li_vn_mode = data[0]
    leap = (li_vn_mode >> 6) & 0b11
    mode = li_vn_mode & 0b111
    stratum = data[1]

    if mode != 4:
        raise NTPResponseError(f"Invalid NTP mode in response: {mode}")
    if leap == 3:
        raise NTPResponseError("NTP server clock unsynchronized (LI=3)")
    if stratum == 0:
        raise NTPResponseError("NTP Kiss-o'-Death or unspecified stratum (stratum=0)")

    originate_sec, originate_frac = struct.unpack("!II", data[24:32])
    if (originate_sec, originate_frac) != (req_sec, req_frac):
        raise NTPResponseError("NTP originate timestamp mismatch")


def query_ntp(host: str, timeout: float = 2.0) -> Sample:
    """Query one NTP server and compute delay/offset using 4-timestamp equations."""
    addr = (host, NTP_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    packet = bytearray(48)
    packet[0] = 0x23  # LI=0, VN=4, Mode=3(client)

    t1 = time.time()
    t1_ntp = _system_to_ntp(t1)
    req_sec = int(t1_ntp)
    req_frac = int((t1_ntp - req_sec) * (2**32))
    struct.pack_into("!II", packet, 40, req_sec, req_frac)

    try:
        sock.sendto(packet, addr)
        data, _ = sock.recvfrom(512)
        t4 = time.time()
    finally:
        sock.close()

    _validate_ntp_response(data, req_sec=req_sec, req_frac=req_frac)
    u = struct.unpack("!12I", data[:48])

    t2_ntp = u[8] + (u[9] / 2**32)   # receive timestamp
    t3_ntp = u[10] + (u[11] / 2**32)  # transmit timestamp

    t2 = _ntp_to_system(t2_ntp)
    t3 = _ntp_to_system(t3_ntp)

    delay = (t4 - t1) - (t3 - t2)
    offset = ((t2 - t1) + (t3 - t4)) / 2

    return Sample(offset_ms=offset * 1000.0, delay_ms=delay * 1000.0)


def collect_stats(
    servers: list[str],
    samples: int = 5,
    timeout: float = 2.0,
    sleep_between: float = 0.5,
) -> list[Stats]:
    """servers 각 서버를 samples번 측정해서 통계를 반환."""
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    if sleep_between < 0:
        raise ValueError("sleep_between must be >= 0")

    raw: dict[str, list[Sample]] = {s: [] for s in servers}
    fails: dict[str, int] = {s: 0 for s in servers}

    for i in range(samples):
        for s in servers:
            try:
                raw[s].append(query_ntp(s, timeout=timeout))
            except (socket.timeout, socket.gaierror, OSError, struct.error, NTPResponseError):
                fails[s] += 1
        if i != samples - 1 and sleep_between > 0:
            time.sleep(sleep_between)

    out: list[Stats] = []
    for s in servers:
        ok = len(raw[s])
        fail = fails[s]
        if ok == 0:
            continue

        offs = [x.offset_ms for x in raw[s]]
        dels = [x.delay_ms for x in raw[s]]

        out.append(
            Stats(
                server=s,
                ok=ok,
                fail=fail,
                avg_offset_ms=mean(offs),
                std_offset_ms=pstdev(offs) if ok > 1 else 0.0,
                avg_delay_ms=mean(dels),
                std_delay_ms=pstdev(dels) if ok > 1 else 0.0,
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
    base_stat = next((x for x in stats if x.server == base), None)
    if base_stat is None:
        raise RuntimeError(f"Base server '{base}' stats not found (측정 실패/목록 누락).")

    ranked: list[Ranked] = []
    for st in stats:
        if not allow_base and st.server == base:
            continue

        vs_base = st.avg_offset_ms - base_stat.avg_offset_ms
        score = abs(vs_base) + (w_delay * st.avg_delay_ms) + (w_jitter * st.std_offset_ms)

        if max_delay_ms is not None and st.avg_delay_ms >= max_delay_ms:
            continue

        ranked.append(
            Ranked(
                server=st.server,
                ok=st.ok,
                fail=st.fail,
                avg_offset_ms=st.avg_offset_ms,
                std_offset_ms=st.std_offset_ms,
                avg_delay_ms=st.avg_delay_ms,
                std_delay_ms=st.std_delay_ms,
                vs_base_ms=vs_base,
                score=score,
                grade=grade(score),
            )
        )

    ranked.sort(key=lambda x: x.score)
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
    for r in ranked:
        if r.server == base:
            continue
        total = r.ok + r.fail
        ok_rate = (r.ok / total) if total > 0 else 0.0
        if ok_rate >= require_ok_rate:
            return r
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
    for idx, item in enumerate(rows, start=1):
        lines.append(
            f"{idx:<4} {item.server:<22} {item.score:>8.2f} {item.grade:>5} "
            f"{item.vs_base_ms:>12.2f} {item.avg_delay_ms:>10.2f} {item.ok:>2}/{item.fail:<5}"
        )
    return "\n".join(lines)
