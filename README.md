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

print(best)
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

- jitter (std_offset_ms)**
  오프셋 변동성 (작을수록 안정적)

---

## Network Requirements

- NTP는 UDP 123 포트를 사용합니다.
- 회사/기관 네트워크에서 차단될 수 있습니다.
- 방화벽 또는 보안 장비 설정에 따라 응답이 실패할 수 있습니다.

---

## License

MIT License
