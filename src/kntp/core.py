"""Core logic for KRISS-based NTP comparison and recommendation."""

from __future__ import annotations

import math
import socket
import struct
import time
from dataclasses import dataclass
from collections.abc import Callable
from statistics import mean, median, pstdev

NTP_PORT = 123
NTP_DELTA = 2208988800
NTP_ERA_SECONDS = 2**32

MAX_SERVERS = 64
MAX_SAMPLES = 50
MAX_TIMEOUT = 10.0
MAX_SLEEP_BETWEEN = 5.0
MAX_TOTAL_PROBES = 240  # DEFAULT_SERVERS(12) x 20 samples
MAX_RECOMMEND_COUNT = 10

# 장기 측정(monitor) 제한. 공용 NTP 서버에 부담을 주지 않도록 간격의 하한을 둔다.
MIN_MONITOR_INTERVAL = 16.0  # chrony minpoll 4와 같은 값
MAX_MONITOR_INTERVAL = 3600.0
DEFAULT_MONITOR_INTERVAL = 64.0  # NTP 클라이언트의 기본 최소 폴링 간격
MAX_MONITOR_ROUNDS = 20160  # 16초 간격으로 약 3.7일, 64초 간격으로 약 2주
MIN_DETREND_SERVERS = 3

OFFSET_METHODS = ("mean", "min_delay")

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


class NTPKissOfDeathError(NTPResponseError):
    """Raised when the server answers with a Kiss-o'-Death packet (stratum 0).

    ``code`` holds the 4-character kiss code such as ``"RATE"``, ``"DENY"`` or ``"RSTR"``.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"NTP Kiss-o'-Death received: {code or '(empty)'}")


# 이 kiss code를 받으면 해당 서버에 더 이상 요청하지 않는다 (RFC 5905 7.4).
STOP_KISS_CODES = frozenset({"RATE", "DENY", "RSTR"})


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
    min_delay_offset_ms: float | None = None
    """delay가 가장 작았던 샘플의 offset. 경로 지연·비대칭 영향이 가장 적은 값."""
    min_delay_ms: float | None = None
    """가장 작은 delay(min_delay_offset_ms를 얻은 샘플의 delay)."""


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
    base: str | None = None
    """이 결과를 계산할 때 기준으로 쓴 서버. recommend()가 기준 서버를 건너뛰는 데 사용."""
    uncertainty_ms: float | None = None
    """vs_base_ms의 오차 상한(±). 경로 비대칭 때문에 생길 수 있는 최대 오차로,
    (이 서버의 delay + 기준 서버의 delay) / 2 이다. |vs_base_ms|가 이 값보다 작으면
    기준 서버와의 차이를 측정으로 구분할 수 없다는 뜻이다."""


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
    if stratum == 0:
        kiss_code = data[12:16].decode("ascii", errors="replace").rstrip("\x00 ")
        raise NTPKissOfDeathError(kiss_code)
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
        # 왕복 시간은 시스템 시각 조정의 영향을 받지 않는 단조 시계로 잰다.
        p1 = time.perf_counter()
        sock.send(packet)
        data = sock.recv(512)
        p4 = time.perf_counter()
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

    delay = (p4 - p1) - (t3 - t2)
    # offset은 서버 시각과 비교해야 하므로 벽시계(t1, t4)를 쓴다.
    # t4는 t1 + 실제 경과 시간으로 고정해, 측정 중 시계가 조정돼도 값이 튀지 않게 한다.
    t4 = t1 + (p4 - p1)
    offset = ((t2 - t1) + (t3 - t4)) / 2
    if not math.isfinite(delay) or not math.isfinite(offset):
        raise NTPResponseError("NTP calculation produced a non-finite value")
    if delay < -0.001:
        raise NTPResponseError("NTP calculation produced an invalid negative delay")

    return Sample(offset_ms=offset * 1000.0, delay_ms=max(0.0, delay * 1000.0))


def _normalize_servers(servers: list[str]) -> list[str]:
    """Validate the server list and drop duplicates while keeping order."""
    if isinstance(servers, (str, bytes)):
        raise ValueError("servers must be a list of host names, not a single string")
    try:
        items = list(servers)
    except TypeError:
        raise ValueError("servers must be a list of host names") from None
    for host in items:
        if not isinstance(host, str) or not host.strip():
            raise ValueError("each server must be a non-empty string")
        if len(host) > 253:
            raise ValueError(f"server name must be at most 253 characters: {host[:40]}...")
    return list(dict.fromkeys(items))


def collect_stats(
    servers: list[str],
    samples: int = 5,
    timeout: float = 2.0,
    sleep_between: float = 0.5,
) -> list[Stats]:
    """servers 각 서버를 samples번 측정해서 통계를 반환.

    - 중복된 서버는 한 번만 측정한다.
    - DNS 실패, 타임아웃, 잘못된 응답, 잘못된 형식의 호스트 이름은 해당 서버의 실패로 집계한다.
    - 서버가 RATE/DENY/RSTR Kiss-o'-Death를 보내면 그 서버에는 더 요청하지 않고,
      남은 회차는 실패로 집계한다.
    """
    servers = _normalize_servers(servers)
    if not 1 <= len(servers) <= MAX_SERVERS:
        raise ValueError(f"servers count must be between 1 and {MAX_SERVERS}")
    if not 1 <= samples <= MAX_SAMPLES:
        raise ValueError(f"samples must be between 1 and {MAX_SAMPLES}")
    if len(servers) * samples > MAX_TOTAL_PROBES:
        max_for_count = MAX_TOTAL_PROBES // len(servers)
        raise ValueError(
            f"servers * samples must be <= {MAX_TOTAL_PROBES} "
            f"(got {len(servers)} * {samples}; use samples <= {max_for_count} "
            f"for {len(servers)} servers)"
        )
    if not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be > 0 and <= {MAX_TIMEOUT}")
    if not 0 <= sleep_between <= MAX_SLEEP_BETWEEN:
        raise ValueError(f"sleep_between must be between 0 and {MAX_SLEEP_BETWEEN}")

    raw: dict[str, list[Sample]] = {s: [] for s in servers}
    fails: dict[str, int] = {s: 0 for s in servers}
    stopped: set[str] = set()

    for i in range(samples):
        _probe_round(servers, timeout, raw, fails, stopped)
        if i != samples - 1 and sleep_between > 0:
            time.sleep(sleep_between)

    return _build_stats(servers, raw, fails)


def _probe_round(
    servers: list[str],
    timeout: float,
    raw: dict[str, list[Sample]],
    fails: dict[str, int],
    stopped: set[str],
) -> dict[str, Sample]:
    """모든 서버를 한 번씩 측정해 raw/fails/stopped를 갱신하고, 이번 회차의 성공 샘플을 반환."""
    got: dict[str, Sample] = {}
    for server in servers:
        if server in stopped:
            fails[server] += 1
            continue
        try:
            sample = query_ntp(server, timeout=timeout)
        except NTPKissOfDeathError as exc:
            fails[server] += 1
            if exc.code in STOP_KISS_CODES:
                stopped.add(server)
            continue
        except (OSError, struct.error, NTPResponseError, UnicodeError):
            # OSError는 socket.timeout / socket.gaierror를 포함한다.
            # UnicodeError는 IDNA로 인코딩할 수 없는 호스트 이름(예: 64자 넘는 label)에서 난다.
            fails[server] += 1
            continue
        raw[server].append(sample)
        got[server] = sample
    return got


def _build_stats(
    servers: list[str],
    raw: dict[str, list[Sample]],
    fails: dict[str, int],
    jitter_offsets: dict[str, list[float]] | None = None,
) -> list[Stats]:
    out: list[Stats] = []
    for server in servers:
        ok = len(raw[server])
        if ok == 0:
            continue
        offsets = [sample.offset_ms for sample in raw[server]]
        delays = [sample.delay_ms for sample in raw[server]]
        best_sample = min(raw[server], key=lambda sample: sample.delay_ms)
        detrended = jitter_offsets.get(server, []) if jitter_offsets else []
        if len(detrended) >= 2:
            std_offset = pstdev(detrended)
        else:
            std_offset = pstdev(offsets) if ok > 1 else 0.0
        out.append(
            Stats(
                server=server,
                ok=ok,
                fail=fails[server],
                avg_offset_ms=mean(offsets),
                std_offset_ms=std_offset,
                avg_delay_ms=mean(delays),
                std_delay_ms=pstdev(delays) if ok > 1 else 0.0,
                min_delay_offset_ms=best_sample.offset_ms,
                min_delay_ms=best_sample.delay_ms,
            )
        )
    return out


def monitor(
    servers: list[str],
    *,
    rounds: int,
    interval: float = DEFAULT_MONITOR_INTERVAL,
    timeout: float = 2.0,
    on_round: Callable[[int, int, list[Stats]], None] | None = None,
) -> list[Stats]:
    """긴 시간 동안 interval초마다 모든 서버를 한 번씩, 총 rounds회 측정해 통계를 반환.

    collect_stats()가 짧은 시간에 몰아서 재는 것과 달리, 시간대별 네트워크 상태 변화까지
    반영해 오래 안정적인 서버를 고를 수 있다. 예: interval=64, rounds=225 → 약 4시간.

    - 각 회차는 시작 시각 기준 interval 간격으로 실행한다(측정에 걸린 시간만큼 밀리지 않음).
    - 오래 재는 동안 이 컴퓨터의 시계 자체가 흘러가면(drift) 모든 서버의 offset이 함께 움직인다.
      이 흔들림이 서버의 jitter로 잡히지 않도록, 응답한 서버가 3개 이상인 회차는 그 회차
      offset의 중앙값을 뺀 값으로 std_offset_ms를 계산한다. avg_offset_ms는 원래 값 그대로다.
    - on_round(완료한 회차, 전체 회차, 지금까지의 통계)를 회차마다 호출한다. 진행 표시나
      중간 저장에 쓸 수 있고, 중간에 멈춰도 마지막으로 받은 통계를 그대로 쓸 수 있다.
    - 중복 제거, 실패 집계, Kiss-o'-Death 처리는 collect_stats()와 같다.
    """
    servers = _normalize_servers(servers)
    if not 1 <= len(servers) <= MAX_SERVERS:
        raise ValueError(f"servers count must be between 1 and {MAX_SERVERS}")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= MAX_MONITOR_ROUNDS:
        raise ValueError(f"rounds must be an integer between 1 and {MAX_MONITOR_ROUNDS}")
    if not MIN_MONITOR_INTERVAL <= interval <= MAX_MONITOR_INTERVAL:
        raise ValueError(
            f"interval must be between {MIN_MONITOR_INTERVAL:g} and {MAX_MONITOR_INTERVAL:g} seconds"
        )
    if not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be > 0 and <= {MAX_TIMEOUT}")

    raw: dict[str, list[Sample]] = {s: [] for s in servers}
    fails: dict[str, int] = {s: 0 for s in servers}
    stopped: set[str] = set()
    detrended: dict[str, list[float]] = {s: [] for s in servers}

    start = time.monotonic()
    for index in range(rounds):
        if index:
            wait = start + index * interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        got = _probe_round(servers, timeout, raw, fails, stopped)
        if len(got) >= MIN_DETREND_SERVERS:
            center = median(sample.offset_ms for sample in got.values())
            for server, sample in got.items():
                detrended[server].append(sample.offset_ms - center)
        if on_round is not None:
            on_round(index + 1, rounds, _build_stats(servers, raw, fails, detrended))

    return _build_stats(servers, raw, fails, detrended)


def _ok_rate(ok: int, fail: int) -> float:
    total = ok + fail
    return (ok / total) if total > 0 else 0.0


def rank_servers(
    stats: list[Stats],
    base: str = DEFAULT_BASE,
    *,
    w_delay: float = 0.20,
    w_jitter: float = 0.50,
    max_delay_ms: float | None = 100.0,
    allow_base: bool = True,
    offset_method: str = "mean",
    min_base_ok_rate: float = 0.5,
) -> list[Ranked]:
    """기준(base) 대비 점수화해 정렬한 리스트 반환.

    offset_method:
        "mean"      - 모든 성공 샘플의 평균 offset으로 기준과 비교(기본값).
        "min_delay" - delay가 가장 작은 샘플의 offset으로 비교. 혼잡·경로 비대칭으로
                      튄 샘플의 영향을 줄이며, 일반적인 NTP 클라이언트의 방식에 가깝다.

    min_base_ok_rate:
        기준 서버의 최소 성공률. 이보다 낮으면 기준값을 믿을 수 없으므로 RuntimeError.

    각 결과의 uncertainty_ms에는 vs_base_ms의 오차 상한(±)을 기록한다. NTP는 왕복 경로가
    대칭이라고 가정하므로, 경로가 비대칭이면 offset이 최대 delay/2만큼 틀릴 수 있다.

    jitter 항목은 offset 표준편차를 쓴다. 성공 샘플이 1개뿐이면 표준편차를 알 수 없으므로
    NTP 오차 상한인 delay/2를 대신 쓴다(샘플이 적은 서버가 유리해지지 않도록).
    """
    if offset_method not in OFFSET_METHODS:
        raise ValueError(f"offset_method must be one of {OFFSET_METHODS}")
    if w_delay < 0 or w_jitter < 0:
        raise ValueError("w_delay and w_jitter must be >= 0")
    if max_delay_ms is not None and max_delay_ms <= 0:
        raise ValueError("max_delay_ms must be > 0 when provided")
    if not 0.0 <= min_base_ok_rate <= 1.0:
        raise ValueError("min_base_ok_rate must be between 0.0 and 1.0")

    base_stat = next((item for item in stats if item.server == base), None)
    if base_stat is None:
        raise RuntimeError(f"Base server '{base}' stats not found (측정 실패/목록 누락).")
    base_rate = _ok_rate(base_stat.ok, base_stat.fail)
    if base_rate < min_base_ok_rate:
        raise RuntimeError(
            f"Base server '{base}' success rate {base_rate:.0%} is below "
            f"min_base_ok_rate {min_base_ok_rate:.0%} (기준값을 신뢰할 수 없음)."
        )

    def offset_of(item: Stats) -> float:
        if offset_method == "min_delay" and item.min_delay_offset_ms is not None:
            return item.min_delay_offset_ms
        return item.avg_offset_ms

    def delay_of(item: Stats) -> float:
        if offset_method == "min_delay" and item.min_delay_ms is not None:
            return item.min_delay_ms
        return item.avg_delay_ms

    def jitter_of(item: Stats) -> float:
        if item.ok > 1:
            return item.std_offset_ms
        return item.avg_delay_ms / 2

    base_offset = offset_of(base_stat)
    base_half_delay = delay_of(base_stat) / 2
    ranked: list[Ranked] = []
    for item in stats:
        if not allow_base and item.server == base:
            continue
        if max_delay_ms is not None and item.avg_delay_ms >= max_delay_ms:
            continue
        vs_base = offset_of(item) - base_offset
        if item.server == base:
            uncertainty = 0.0
        else:
            uncertainty = delay_of(item) / 2 + base_half_delay
        score = abs(vs_base) + (w_delay * item.avg_delay_ms) + (w_jitter * jitter_of(item))
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
                base=base,
                uncertainty_ms=uncertainty,
            )
        )
    ranked.sort(key=lambda item: item.score)
    return ranked


def recommend(
    ranked: list[Ranked],
    *,
    base: str | None = None,
    require_ok_rate: float = 0.8,
) -> Ranked | None:
    """랭킹 결과에서 성공률 조건을 만족하는 추천 1개를 반환.

    기준 서버는 추천하지 않는다. base를 생략하면 rank_servers()가 각 결과에 기록한
    기준 서버를 쓰고, 그 기록도 없으면 DEFAULT_BASE를 쓴다.
    """
    if not 0.0 <= require_ok_rate <= 1.0:
        raise ValueError("require_ok_rate must be between 0.0 and 1.0")
    for item in ranked:
        item_base = base if base is not None else (item.base or DEFAULT_BASE)
        if item.server == item_base:
            continue
        if _ok_rate(item.ok, item.fail) >= require_ok_rate:
            return item
    return None


def recommend_set(
    ranked: list[Ranked],
    *,
    count: int = 3,
    base: str | None = None,
    require_ok_rate: float = 0.8,
) -> list[Ranked]:
    """기준 서버와 함께 쓸 보조 서버를 점수 순으로 최대 count개 반환.

    NTP 클라이언트(chrony, ntpd 등)는 서버가 3~4개 이상일 때 이상한 서버(falseticker)를
    걸러낼 수 있으므로, 서버 1개 대신 여러 개를 함께 설정하는 것이 권장된다.
    기준 서버는 결과에 넣지 않는다. 조건을 만족하는 서버가 적으면 count보다 적게 반환한다.
    """
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_RECOMMEND_COUNT:
        raise ValueError(f"count must be an integer between 1 and {MAX_RECOMMEND_COUNT}")
    if not 0.0 <= require_ok_rate <= 1.0:
        raise ValueError("require_ok_rate must be between 0.0 and 1.0")
    picked: list[Ranked] = []
    for item in ranked:
        item_base = base if base is not None else (item.base or DEFAULT_BASE)
        if item.server == item_base:
            continue
        if _ok_rate(item.ok, item.fail) >= require_ok_rate:
            picked.append(item)
            if len(picked) == count:
                break
    return picked


def format_ranked_table(ranked: list[Ranked], *, top_n: int | None = 5) -> str:
    """Return a readable text table for ranking results."""
    if top_n is not None and top_n < 1:
        raise ValueError("top_n must be >= 1 when provided")
    rows = ranked[:top_n] if top_n is not None else ranked
    if not rows:
        return "(no ranked results)"

    name_w = max(22, *(len(item.server) for item in rows))
    header = (
        f"{'rank':<4} {'server':<{name_w}} {'score':>8} {'grade':>5} "
        f"{'vs_base(ms)':>12} {'±(ms)':>8} {'delay(ms)':>10} {'ok/fail':>8}"
    )
    lines = [header, "-" * len(header)]
    for index, item in enumerate(rows, start=1):
        ok_fail = f"{item.ok}/{item.fail}"
        unc = f"{item.uncertainty_ms:.2f}" if item.uncertainty_ms is not None else "-"
        lines.append(
            f"{index:<4} {item.server:<{name_w}} {item.score:>8.2f} {item.grade:>5} "
            f"{item.vs_base_ms:>12.2f} {unc:>8} {item.avg_delay_ms:>10.2f} {ok_fail:>8}"
        )
    return "\n".join(lines)
