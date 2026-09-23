"""alobo-bot command line: `find` (cheapest courts and tickets) and `serve` (HTTP viewer)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

from . import __version__
from .api import AloboClient, ApiError
from .config import load_config, validate
from .pricing import ClockError, parse_clock
from .report import render_text, to_dict, write_report
from .search import build_query, find_cheapest


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="alobo-bot", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=pathlib.Path, help="config.yaml path (default: project root)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_find = sub.add_parser("find", help="rank the cheapest courts for a place and time window")
    p_find.add_argument("--place", help="area or saved-place name to search, e.g. \"Cầu Giấy, Hà Nội\"")
    p_find.add_argument("--lat", type=float, help="latitude; needs --lng and uses --radius")
    p_find.add_argument("--lng", type=float, help="longitude; needs --lat")
    p_find.add_argument("--radius", type=float, default=None, metavar="KM",
                        help="search radius in km when using --lat/--lng (default from config)")
    p_find.add_argument("--preset", metavar="NAME",
                        help="saved place from config (search.presets); --place NAME finds "
                             "the same spot, and --lat/--lng override both")
    p_find.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="day to price (default: today)")
    p_find.add_argument("--from", dest="time_from", default="18:00", metavar="HH:MM",
                        help="window start (default: 18:00)")
    p_find.add_argument("--to", dest="time_to", default="21:00", metavar="HH:MM",
                        help="window end (default: 21:00)")
    p_find.add_argument("--sport", default=None, help="sport key (default from config: pickleball)")
    p_find.add_argument("--category", choices=("all", "court", "social"), default=None,
                        help="which categories to report: all (default), court (per court), "
                             "or social (\"xé vé\": tickets, per person)")
    p_find.add_argument("--availability", choices=("any", "free"), default=None,
                        help="court availability: any (default) keeps courts that are only "
                             "partly free and quotes them for the open part; free keeps only "
                             "courts free for the whole window")
    p_find.add_argument("--target", default=None, metavar="ID|NAME",
                        help="tariff (\"đối tượng áp dụng\") to price courts under, e.g. kh; "
                             "default: each court type's generic customer tariff")
    p_find.add_argument("--limit", type=int, default=None,
                        help="max branches to price (default from config)")
    p_find.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p_find.add_argument("--out", action="store_true",
                        help="also write data/reports/cheapest.{md,json} and a dated raw snapshot")
    p_find.set_defaults(func=_cmd_find)

    p_sports = sub.add_parser("sports", help="list the sport types the API exposes")
    p_sports.add_argument("--json", action="store_true")
    p_sports.set_defaults(func=_cmd_sports)

    p_serve = sub.add_parser("serve", help="run the FastAPI report viewer")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8085)
    p_serve.set_defaults(func=_cmd_serve)
    return parser.parse_args(argv)


def _load_config(args: argparse.Namespace) -> dict:
    cfg = load_config(args.config)
    validate(cfg)
    return cfg


def _cmd_find(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    if (args.lat is None) != (args.lng is None):
        print("--lat and --lng must be given together", file=sys.stderr)
        return 2
    try:
        day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
        query = build_query(
            cfg,
            place=args.place,
            preset=args.preset,
            latitude=args.lat,
            longitude=args.lng,
            radius_km=args.radius,
            day=day,
            start_minute=parse_clock(args.time_from),
            end_minute=parse_clock(args.time_to),
            sport=args.sport,
            max_branches=args.limit,
            category=args.category,
            availability=args.availability,
            target=args.target,
        )
    except (ClockError, ValueError) as exc:
        print(f"invalid argument: {exc}", file=sys.stderr)
        return 2

    if query.start_minute == query.end_minute:
        print("--from and --to must differ", file=sys.stderr)
        return 2

    try:
        result = find_cheapest(cfg, query)
    except (ApiError, ValueError) as exc:
        print(f"find failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    if args.out:
        md_path, json_path = write_report(result, cfg)
        print(f"\nReport: {md_path}\nRaw:    {json_path}", file=sys.stderr)
    return 0


def _cmd_sports(args: argparse.Namespace) -> int:
    cfg = _load_config(args)
    sports = AloboClient(cfg).sport_types()
    if args.json:
        print(json.dumps(
            [{"key": s.key, "name": s.name, "intValue": s.int_value} for s in sports],
            ensure_ascii=False, indent=2,
        ))
    else:
        for sport in sorted(sports, key=lambda s: -s.priority):
            print(f"{sport.key:12} {sport.name:16} intValue={sport.int_value}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .web import create_app

    import uvicorn

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
