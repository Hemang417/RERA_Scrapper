"""
--verify mode: since the "observed" category endpoints in config.py were
taken from real (but not individually re-tested) network traffic, this
calls each one against a real project id and reports whether it actually
returns usable data -- without guessing any new URLs.
"""

import json
import os

import requests

import config
import api_client


def verify_endpoints(
    project_id: str,
    out_dir: str,
    token: str | None = None,
    endpoints: dict | None = None,
    category_order: list | None = None,
) -> dict:
    """`endpoints`/`category_order` default to MahaRERA's config tables, so
    the existing --verify path is unchanged; a second state's adapter passes
    its own."""
    endpoints = endpoints if endpoints is not None else config.CATEGORY_ENDPOINTS
    category_order = category_order if category_order is not None else config.CATEGORY_ORDER
    os.makedirs(out_dir, exist_ok=True)
    report = {}

    with requests.Session() as session:
        for category in category_order:
            endpoint = endpoints[category]
            try:
                data = api_client.fetch_category(category, project_id, session, token)
                is_empty = data in (None, [], {})
                report[category] = {
                    "path": endpoint["path"],
                    "status": endpoint["status"],
                    "outcome": "empty" if is_empty else "has data",
                }
                with open(os.path.join(out_dir, f"{category}.json"), "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except api_client.CategoryFetchError as e:
                report[category] = {
                    "path": endpoint["path"],
                    "status": endpoint["status"],
                    "outcome": f"FAILED: {e}",
                }

    with open(os.path.join(out_dir, "_verify_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'category':<20} {'config status':<12} outcome")
    for category, info in report.items():
        print(f"{category:<20} {info['status']:<12} {info['outcome']}")
    print(f"\nRaw responses written to {out_dir}/")

    return report
