[![PyPI](https://img.shields.io/pypi/v/kntp-lib?style=flat-square)](https://pypi.org/project/kntp-lib/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/kntp-lib/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

# kntp-lib

KRISS(한국표준과학연구원)를 기준으로 NTP 서버의 정확도, 지연 및 안정성을 분석하고 적합한 서버를 추천하는 Python 라이브러리입니다.

## 주요 기능

- NTP 4-timestamp 공식 기반 offset 및 delay 계산
- IPv4와 IPv6 UDP 연결 지원
- 응답 출발지 고정과 NTP v3/v4·비동기 stratum·비정상 timestamp 검증
- 2036년 NTP era rollover 처리
- 서버별 평균·표준편차, 점수 기반 순위와 A–D 등급
- 성공률 조건을 적용한 추천 서버 선택

## 설치

```bash
pip install kntp-lib
```

## 빠른 시작

```python
import kntp

stats = kntp.collect_stats(
    kntp.DEFAULT_SERVERS,
    samples=7,
    timeout=2.0,
    sleep_between=0.3,
)
ranked = kntp.rank_servers(
    stats,
    base=kntp.DEFAULT_BASE,
    w_delay=0.20,
    w_jitter=0.50,
    max_delay_ms=120.0,
)
best = kntp.recommend(ranked, require_ok_rate=0.8)

print(kntp.format_ranked_table(ranked, top_n=5))
print("추천:", best.server if best else "None")
```

권장 시작값은 `samples=7`, `timeout=2.0`, `sleep_between=0.3`, `require_ok_rate=0.8`입니다.

## 예외 처리

```python
import kntp

try:
    stats = kntp.collect_stats(kntp.DEFAULT_SERVERS, samples=7, timeout=2.0)
    ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE)
    best = kntp.recommend(ranked, require_ok_rate=0.8)
except ValueError as exc:
    print("입력값 오류:", exc)
except RuntimeError as exc:
    print("기준 서버 측정 실패:", exc)
else:
    print("추천 서버:", best.server if best else "없음")
```

개별 서버의 DNS, 타임아웃, 잘못된 NTP 응답은 실패 횟수로 집계됩니다. 기준 서버 측정이 모두 실패하면 `rank_servers()`가 `RuntimeError`를 발생시킵니다.

## API 제약사항

- 서버 수: 1–64개
- `samples`: 1–50
- 서버 수 × `samples`: 최대 100회
- `timeout`: 0초 초과, 10초 이하
- `sleep_between`: 0–5초
- `rank_servers(): w_delay >= 0, w_jitter >= 0, max_delay_ms > 0 또는 None`
- `recommend(): 0.0 <= require_ok_rate <= 1.0`
- `format_ranked_table(): top_n >= 1 또는 None`

## 사용자 서버 목록

```python
servers = ["ntp.kriss.re.kr", "kr.pool.ntp.org", "time.bora.net"]
stats = kntp.collect_stats(servers, samples=7)
ranked = kntp.rank_servers(stats, base="ntp.kriss.re.kr")
```

## 네트워크 및 보안

- NTP는 UDP 123 포트를 사용하므로 방화벽에서 차단될 수 있습니다.
- 연결된 UDP 소켓과 originate timestamp 검증으로 다른 출발지 및 요청과 무관한 응답을 거부합니다.
- 일반 NTP에는 암호학적 서버 인증이 없습니다. 공격자에 대한 인증이 필요한 환경에서는 NTS(Network Time Security)를 사용해야 합니다.
- 측정 결과를 인증서, 토큰 만료, 금융 거래, 감사 로그처럼 보안상 중요한 시각 판단에 사용하지 마세요.
- 이 라이브러리는 시스템 시계를 변경하지 않고 측정·비교만 수행합니다.
- 외부 사용자가 서버 목록이나 측정 횟수를 지정하는 서비스에서는 애플리케이션 계층의 추가 요청 제한도 적용하세요.

## 테스트

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 버전 보존

이전 소스 버전은 `Versions/<버전>`에 보존합니다. 보존 버전은 기록용이며 최신 보안 수정이 포함되지 않을 수 있습니다. 최신 패키지 소스는 저장소 기본 위치에 있습니다.

## 라이선스

MIT License
