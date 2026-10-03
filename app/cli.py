"""Command line: python -m app.cli {profile,crawl,stats,serve}"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import config
from .pipeline import PipelineBusy, ensure_profile, rescore, run_pipeline
from .resume import ResumeError
from .store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs", description="Job discovery pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profile", help="(Re)build profile from a resume")
    p.add_argument("--resume", default=None, help=f"PDF/TXT path (default {config.DEFAULT_RESUME})")

    c = sub.add_parser("crawl", help="Fetch all sources, store and score jobs")
    c.add_argument("--resume", default=None)
    c.add_argument("--rebuild-profile", action="store_true")
    c.add_argument("--quiet", action="store_true")

    sub.add_parser("stats", help="Print database statistics")

    s = sub.add_parser("serve", help="Run the dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    store = Store()

    try:
        if args.cmd == "profile":
            profile = ensure_profile(store, args.resume, force=True)
            n = rescore(store, profile)
            print(f"Profile saved: {len(profile['skills'])} skills, level={profile['level']}; rescored {n} jobs")
        elif args.cmd == "crawl":
            progress = None if args.quiet else (lambda m: print("  ", m, flush=True))
            stats = run_pipeline(store, resume=args.resume, rebuild_profile=args.rebuild_profile, on_progress=progress)
            print(json.dumps({k: v for k, v in stats.items() if k != "errors"}, indent=2))
            if stats["errors"]:
                print(f"{len(stats['errors'])} source(s) failed:", *stats["errors"][:10], sep="\n  ")
        elif args.cmd == "stats":
            print(json.dumps(store.stats(), indent=2, default=str))
        elif args.cmd == "serve":
            import uvicorn

            uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    except PipelineBusy as e:
        print(f"skipped: {e}", file=sys.stderr)
        return 3
    except ResumeError as e:
        print(f"resume error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
