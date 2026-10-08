"""Resilient production-style usage example with fallback handling."""

import kntp


def main() -> None:
    try:
        stats = kntp.collect_stats(kntp.DEFAULT_SERVERS, samples=7, timeout=2.0, sleep_between=0.2)
        ranked = kntp.rank_servers(stats, base=kntp.DEFAULT_BASE, max_delay_ms=120.0)
        best = kntp.recommend(ranked, require_ok_rate=0.8)

        print(kntp.format_ranked_table(ranked, top_n=7))

        if best is None:
            print("\nNo recommendation. Try lowering require_ok_rate or increasing samples.")
        else:
            print(f"\nRecommended server: {best.server} (grade={best.grade}, score={best.score:.2f})")

    except ValueError as exc:
        print("Input configuration error:", exc)
    except RuntimeError as exc:
        print("Ranking failed. Check base server availability:", exc)


if __name__ == "__main__":
    main()
