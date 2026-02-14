[![PyPI](https://img.shields.io/pypi/v/kntp-lib.svg)](https://pypi.org/project/kntp-lib/)
[![Python](https://img.shields.io/pypi/pyversions/kntp-lib.svg)](https://pypi.org/project/kntp-lib/)
[![License](https://img.shields.io/pypi/l/kntp-lib.svg)](https://pypi.org/project/kntp-lib/)

# kntp-lib

KRISS (Korea Research Institute of Standards and Science) 기준으로  
NTP 서버의 정확도, 지연(delay), 안정성(jitter)을 분석하고  
가장 적합한 서버를 추천하는 Python 라이브러리입니다.

---

## Features

- NTP 4-timestamp 공식 기반 offset / delay 계산
- 서버별 통계 분석 (평균, 표준편차)
- KRISS 기준 상대 오프셋 비교
- 점수 기반 랭킹 및 등급화 (A–D)
- 자동 추천 서버 선택
- 한국 환경 최적화 기본 서버 목록 제공

---

## Install

```bash
pip install kntp-lib
```

---

## Quick Start

```python
import kntp

# 기본 서버 목록 사용
stats = kntp.collect_stats(kntp.DEFAULT_SERVERS, samples=5)

# KRISS 기준으로 랭킹
ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE)

# 가장 추천되는 서버
best = kntp.recommend(ranked)

print(kntp.format_ranked_table(ranked, top_n=5))
print("추천:", best.server if best else "None")
```

---

## 한국 개발자용 실전 권장값

사내망/기관망에서 NTP 응답이 불안정할 수 있으므로, 아래처럼 시작하는 것을 권장합니다.

- `samples=5~10`: 일시적인 네트워크 흔들림을 평균화
- `timeout=1.5~2.5`: 국내망 기준 기본값으로 무난
- `sleep_between=0.2~0.5`: 서버에 과도한 burst 요청 방지
- `require_ok_rate=0.7~0.9`: 운영환경 안정성 기준에 맞춰 조정

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
print(best)
```

---

## 예외 처리 패턴 (운영 코드 권장)

네트워크 환경에 따라 일부 서버는 실패할 수 있으므로, 추천 로직을 안전하게 감싸는 패턴을 권장합니다.

```python
import kntp

try:
    stats = kntp.collect_stats(kntp.DEFAULT_SERVERS, samples=5, timeout=2.0)
    ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE)
    best = kntp.recommend(ranked, require_ok_rate=0.8)

    if best is None:
        print("추천 가능한 서버가 없습니다. require_ok_rate 또는 samples를 조정하세요.")
    else:
        print("추천 서버:", best.server)

except ValueError as e:
    # 파라미터 오류 (samples/timeout/ok_rate 등)
    print("입력값 오류:", e)
except RuntimeError as e:
    # base 서버 측정 실패 등
    print("랭킹 계산 실패:", e)
```

---

## API 파라미터 제약사항

아래 값들은 잘못 입력 시 `ValueError`를 발생시킵니다.

- `collect_stats(samples >= 1, timeout > 0, sleep_between >= 0)`
- `rank_servers(w_delay >= 0, w_jitter >= 0, max_delay_ms > 0 or None)`
- `recommend(0.0 <= require_ok_rate <= 1.0)`

---


## 결과 출력 가독성

`format_ranked_table()`을 사용하면 랭킹 결과를 표 형태로 보기 쉽게 출력할 수 있습니다.

```python
print(kntp.format_ranked_table(ranked, top_n=5))
```

예시 출력:

```text
rank server                     score grade  vs_base(ms)  delay(ms)  ok/fail
---------------------------------------------------------------------------
1    kr.pool.ntp.org             4.12     A        -0.80      12.10   5/0
2    time.bora.net               6.45     B         1.23      15.04   5/0
```

---

## examples 디렉토리

- `examples/quickstart.py`: 기본 측정 + 표 출력
- `examples/resilient_usage.py`: 운영 코드형 예외 처리 + fallback

```bash
PYTHONPATH=src python examples/quickstart.py
PYTHONPATH=src python examples/resilient_usage.py
```

---

## Example Output

```text
Ranked(
    server='kr.pool.ntp.org',
    avg_offset_ms=-0.8,
    avg_delay_ms=12.1,
    score=4.1,
    grade='A'
)
```

---

## Custom Server List

```python
import kntp

servers = [
    "ntp.kriss.re.kr",
    "kr.pool.ntp.org",
    "time.bora.net"
]

stats = kntp.collect_stats(servers, samples=5)
ranked = kntp.rank_servers(stats, base="ntp.kriss.re.kr")

print(ranked[0])
```

---

## Terminology

- offset (ms)  
  기준 서버(KRISS) 대비 시간 차이

- delay (ms)  
  네트워크 왕복 지연 시간 (RTT)

- jitter (std_offset_ms)  
  오프셋 변동성 (작을수록 안정적)

---

## Network Requirements

- NTP는 UDP 123 포트를 사용합니다.
- 회사/기관 네트워크에서 차단될 수 있습니다.
- 방화벽 또는 보안 장비 설정에 따라 응답이 실패할 수 있습니다.

---

## License

MIT License
