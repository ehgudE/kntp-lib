"""Basic runnable example for Korean developers."""

import kntp

stats = kntp.collect_stats(
    kntp.DEFAULT_SERVERS,
    samples=5,
    timeout=2.0,
    sleep_between=0.3,
)

ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE)
secondaries = kntp.recommend_set(ranked, count=3, require_ok_rate=0.8)

print(kntp.format_ranked_table(ranked, top_n=5))
print()
print("Recommended secondary servers (use together with the base):")
for item in secondaries:
    print(f"  {item.server}  vs_base {item.vs_base_ms:+.2f} ms +/- {item.uncertainty_ms:.2f} ms")
