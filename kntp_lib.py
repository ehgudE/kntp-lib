# kntp_lib.py
# 한국 NTP 비교/추천용 라이브러리 코어
# Python 3.10+ (3.14 OK)

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Dict, List, Optional

NTP_PORT = 123
NTP_DELTA = 2208988800  # seconds between 1900-01-01 and 1970-01-01

DEFAULT_BASE = "ntp.kriss.re.kr"

DEFAULT_SERVERS: List[str] = [
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


def query_ntp(host: str, timeout: float = 2.0) -> Sample:
    """
    NTP 표준 4타임스탬프 방식(offset/delay) 계산.

    T1: client send time (local)
    T2: server receive time (from packet)
    T3: server transmit time (from packet)
    T4: client receive time (local)

    delay  = (T4 - T1) - (T3 - T2)
    offset = ((T2 - T1) + (T3 - T4)) / 2
    """
    addr = (host, NTP_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    packet = bytearray(48)
    packet[0] = 0x23  # LI=0, VN=4, Mode=3(client)

    # client transmit timestamp 채움(서버가 originate 검증에 활용 가능)
    t1 = time.time()
    t1_ntp = _system_to_ntp(t1)
    sec = int(t1_ntp)
    frac = int((t1_ntp - sec) * (2**32))
    struct.pack_into("!II", packet, 40, sec, frac)

    try:
        sock.sendto(packet, addr)
        data, _ = sock.recvfrom(512)
        t4 = time.time()
    finally:
        sock.close()

    if len(data) < 48:
        raise ValueError("NTP response too short")

    u = struct.unpack("!12I", data[:48])

    # T2 (recv): words 8,9 / T3 (tx): words 10,11
    t2_ntp = u[8] + (u[9] / 2**32)
    t3_ntp = u[10] + (u[11] / 2**32)

    t2 = _ntp_to_system(t2_ntp)
    t3 = _ntp_to_system(t3_ntp)

    delay = (t4 - t1) - (t3 - t2)
    offset = ((t2 - t1) + (t3 - t4)) / 2

    return Sample(offset_ms=offset * 1000.0, delay_ms=delay * 1000.0)


def collect_stats(
    servers: List[str],
    samples: int = 5,
    timeout: float = 2.0,
    sleep_between: float = 0.5,
) -> List[Stats]:
    """
    servers 각 서버를 samples번 측정해서 통계를 반환.
    """
    if samples < 1:
        raise ValueError("samples must be >= 1")

    raw: Dict[str, List[Sample]] = {s: [] for s in servers}
    fails: Dict[str, int] = {s: 0 for s in servers}

    for i in range(samples):
        for s in servers:
            try:
                raw[s].append(query_ntp(s, timeout=timeout))
            except Exception:
                fails[s] += 1
        if i != samples - 1 and sleep_between > 0:
            time.sleep(sleep_between)

    out: List[Stats] = []
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
    stats: List[Stats],
    base: str = DEFAULT_BASE,
    *,
    w_delay: float = 0.20,
    w_jitter: float = 0.50,
    max_delay_ms: Optional[float] = 100.0,
    allow_base: bool = True,
) -> List[Ranked]:
    """
    기준(base) 대비 점수화해 정렬한 리스트 반환.

    score(작을수록 좋음) =
        |vs_base_offset| + w_delay*avg_delay + w_jitter*std_offset

    max_delay_ms:
      - None이면 지연 필터링 없음
      - 숫자면 avg_delay_ms가 그 이상인 서버는 제외(추천 대상에서 제외)
    """
    base_stat = next((x for x in stats if x.server == base), None)
    if base_stat is None:
        raise RuntimeError(f"Base server '{base}' stats not found (측정 실패/목록 누락).")

    ranked: List[Ranked] = []
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
    ranked: List[Ranked],
    *,
    base: str = DEFAULT_BASE,
    require_ok_rate: float = 0.8,
) -> Optional[Ranked]:
    """
    랭킹 결과에서 '추천 1개'를 골라 반환.
    - base 제외
    - 성공률(ok/(ok+fail))이 require_ok_rate 미만이면 제외
    """
    for r in ranked:
        if r.server == base:
            continue
        total = r.ok + r.fail
        ok_rate = (r.ok / total) if total > 0 else 0.0
        if ok_rate >= require_ok_rate:
            return r
    return None
