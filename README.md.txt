# kntp-lib

KRISS (Korea Research Institute of Standards and Science) 기준으로  
NTP 서버의 정확도, 지연, 안정성을 분석하고 추천하는 Python 라이브러리입니다.

## Features

- NTP 4-timestamp 공식 기반 offset / delay 계산
- 서버별 통계 분석 (평균, 표준편차)
- KRISS 기준 상대 오프셋 비교
- 점수 기반 랭킹 및 등급화 (A–D)
- 자동 추천 서버 선택

## Usage

```python
import kntp_lib as kntp

stats = kntp.collect_stats(kntp.DEFAULT_SERVERS, samples=5)
ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE)

best = kntp.recommend(ranked)
print(best)
