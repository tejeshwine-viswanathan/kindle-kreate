"""Concurrent-upload load test against a running API.

    python scripts/load_test.py book.pdf --jobs 8 --concurrency 4 [--url http://localhost:8000] [--user u --password p]

Uploads the file N times with the given concurrency, waits for every job, and reports
wall time, per-job latency and page throughput. Delete the jobs afterwards so the
server's disk isn't left full of copies.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def run_job(client: httpx.Client, path: Path, poll: float) -> dict:
    started = time.monotonic()
    with path.open("rb") as f:
        res = client.post("/api/jobs", files={"file": (path.name, f, "application/octet-stream")})
    if res.status_code != 202:
        return {"ok": False, "error": f"{res.status_code}: {res.text[:200]}", "seconds": time.monotonic() - started}
    job_id = res.json()["job_id"]
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(poll)
    client.delete(f"/api/jobs/{job_id}")
    return {
        "ok": job["status"] == "done",
        "error": job.get("error"),
        "seconds": time.monotonic() - started,
        "pages": job["pages_total"],
        "warnings": job["warnings"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--user")
    ap.add_argument("--password")
    args = ap.parse_args()

    auth = (args.user, args.password) if args.user else None
    with httpx.Client(base_url=args.url, timeout=600, auth=auth) as client:
        client.get("/health").raise_for_status()
        t0 = time.monotonic()
        with ThreadPoolExecutor(args.concurrency) as pool:
            results = list(pool.map(lambda _: run_job(client, args.file, args.poll), range(args.jobs)))
        wall = time.monotonic() - t0

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    pages = sum(r.get("pages", 0) for r in ok)
    print(f"{len(ok)}/{len(results)} jobs succeeded in {wall:.1f}s wall time")
    if ok:
        latencies = [r["seconds"] for r in ok]
        print(f"  per job: median {statistics.median(latencies):.1f}s, max {max(latencies):.1f}s")
        print(f"  throughput: {pages / wall:.1f} pages/s ({pages} pages)")
    for r in failed:
        print(f"  FAILED after {r['seconds']:.1f}s: {r['error']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
