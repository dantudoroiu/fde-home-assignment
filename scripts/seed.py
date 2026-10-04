"""Submit the sample tickets to a running app through the public API.

Usage: python scripts/seed.py [--url http://localhost:8000] [--limit 10]
"""

import argparse
import json
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent.parent / "data" / "sample_tickets.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    tickets = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    with httpx.Client(base_url=args.url, timeout=10) as client:
        for t in tickets[: args.limit]:
            resp = client.post(
                "/api/tickets",
                json={"subject": t["subject"], "body": t["body"], "customer_email": t["customer_email"]},
            )
            resp.raise_for_status()
            print(f"{t['id']} -> ticket #{resp.json()['id']}")
    print(f"Submitted {len(tickets[: args.limit])} tickets. Open {args.url} to watch the queue.")


if __name__ == "__main__":
    main()
