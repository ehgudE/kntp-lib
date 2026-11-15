"""Resilient production-style usage example with fallback handling."""

import kntp


def main() -> None:
    try:
        stats = kntp.collect_stats(kntp.DEFAULT_SERVERS, samples=7, timeout=2.0, sleep_between=0.2)
        ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE, max_delay_ms=120.0)
        secondaries = kntp.recommend_set(ranked, count=3, require_ok_rate=0.8)

        print(kntp.format_ranked_table(ranked, top_n=7))

        if not secondaries:
            print("\nNo recommendation. Try lowering require_ok_rate or increasing samples.")
        else:
            print(f"\nRecommended secondary servers (use together with {kntp.DEFAULT_BASE}):")
            for item in secondaries:
                print(
                    f"  {item.server} (grade={item.grade}, score={item.score:.2f}, "
                    f"vs_base={item.vs_base_ms:+.2f} ms +/- {item.uncertainty_ms:.2f} ms)"
                )

    except ValueError as exc:
        print("Input configuration error:", exc)
    except RuntimeError as exc:
        print("Ranking failed. Check base server availability:", exc)


if __name__ == "__main__":
    main()
