"""Basic runnable example for Korean developers."""

import kntp

stats = kntp.collect_stats(
    kntp.DEFAULT_SERVERS,
    samples=5,
    timeout=2.0,
    sleep_between=0.3,
)

ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE)
best = kntp.recommend(ranked, require_ok_rate=0.8)

print(kntp.format_ranked_table(ranked, top_n=5))
print("\nRecommended:", best.server if best else "None")
