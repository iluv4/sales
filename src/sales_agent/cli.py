"""Command-line entry point: `sales-agent`."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from .config import ConfigError, load_config
from .store import LEAD_FIELDS, STAGES, Store

DEFAULT_CONFIG = Path("config.toml")


def _load(args: argparse.Namespace):
    path = Path(args.config) if args.config else (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    if path is not None and not path.exists():
        sys.exit(f"Config file not found: {path}")
    try:
        config = load_config(path)
    except ConfigError as exc:
        sys.exit(f"Config error: {exc}")
    if path is None:
        print("No config.toml found; using defaults. Run `sales-agent init` to create one.", file=sys.stderr)
    return config, Store(config.db_file)


def _show_progress(message: Any) -> None:
    for block in message.content:
        if block.type == "tool_use":
            args = json.dumps(block.input, default=str)
            print(f"  · {block.name} {args[:100]}{'…' if len(args) > 100 else ''}", file=sys.stderr)
        elif block.type == "server_tool_use":
            detail = block.input.get("query") or block.input.get("url") or ""
            print(f"  · {block.name} {detail}", file=sys.stderr)


def _run_turn(agent, text: str) -> None:
    import anthropic

    try:
        result = agent.send(text, on_message=_show_progress)
    except anthropic.AuthenticationError:
        sys.exit("Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except TypeError as exc:
        # The SDK raises TypeError at request time when it finds no credentials at all.
        if "authentication method" not in str(exc):
            raise
        sys.exit("No Anthropic credentials found. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except anthropic.RateLimitError:
        print("Rate limited by the API. Wait a moment and try again.", file=sys.stderr)
        return
    except anthropic.APIStatusError as exc:
        print(f"API error {exc.status_code}: {exc.message}", file=sys.stderr)
        return
    except anthropic.APIConnectionError:
        print("Could not reach the API. Check your network connection.", file=sys.stderr)
        return
    print(f"\n{result.text}\n")


def cmd_init(args: argparse.Namespace) -> None:
    target = Path(args.path)
    if target.exists() and not args.force:
        sys.exit(f"{target} already exists (use --force to overwrite)")
    source = resources.files("sales_agent").joinpath("config.example.toml")
    with resources.as_file(source) as src:
        shutil.copyfile(src, target)
    print(f"Wrote {target}. Fill in [company], [icp] and [outreach], then run `sales-agent chat`.")


def cmd_chat(args: argparse.Namespace) -> None:
    from .agent import SalesAgent

    config, store = _load(args)
    agent = SalesAgent(config, store)
    print(f"Sales agent ({config.agent.model}). Type /help for commands, /quit to exit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/help":
            print("/reset  start a new conversation (the CRM is kept)\n/quit   exit")
            continue
        if text == "/reset":
            agent.reset()
            print("Conversation cleared.")
            continue
        _run_turn(agent, text)


def cmd_ask(args: argparse.Namespace) -> None:
    from .agent import SalesAgent

    config, store = _load(args)
    _run_turn(SalesAgent(config, store), " ".join(args.prompt))


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: min(max(len(c), *(len(str(r.get(c) if r.get(c) is not None else "")) for r in rows)), 40) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for r in rows:
        cells = [str(r.get(c) if r.get(c) is not None else "")[: widths[c]].ljust(widths[c]) for c in columns]
        print("  ".join(cells))


def cmd_leads(args: argparse.Namespace) -> None:
    _, store = _load(args)
    rows = store.search_leads(args.query or "", args.stage or "", args.limit)
    _print_table(rows, ["id", "company", "contact_name", "title", "stage", "score", "fit"])


def cmd_show(args: argparse.Namespace) -> None:
    _, store = _load(args)
    try:
        lead = store.get_lead(args.lead_id)
    except LookupError as exc:
        sys.exit(str(exc))
    for key, value in lead.items():
        if value not in ("", None):
            print(f"{key:>13}: {value}")
    print("\nActivity:")
    for a in store.activities(args.lead_id):
        print(f"  {a['created_at'][:10]}  [{a['kind']}] {a['summary']}")
    print("\nDrafts:")
    for d in store.drafts(lead_id=args.lead_id):
        print(f"  #{d['id']} {d['channel']} ({d['status']}) {d['subject']}")


def cmd_drafts(args: argparse.Namespace) -> None:
    _, store = _load(args)
    if args.mark:
        draft_id, status = args.mark
        try:
            store.set_draft_status(int(draft_id), status)
        except (LookupError, ValueError) as exc:
            sys.exit(str(exc))
        print(f"Draft #{draft_id} marked {status}.")
        return
    for d in store.drafts(status=args.status):
        who = f"{d['contact_name']} @ {d['company']}" if d["contact_name"] else d["company"]
        print(f"── #{d['id']}  {d['channel']}  to {who} (lead {d['lead_id']})  [{d['status']}]")
        if d["subject"]:
            print(f"Subject: {d['subject']}")
        print(f"{d['body']}\n")


def cmd_pipeline(args: argparse.Namespace) -> None:
    _, store = _load(args)
    print(json.dumps(store.pipeline_summary(args.stale_days), indent=2))


def cmd_import(args: argparse.Namespace) -> None:
    _, store = _load(args)
    added = skipped = 0
    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "company" not in [h.strip().lower() for h in reader.fieldnames]:
            sys.exit("CSV needs a 'company' column. Optional: " + ", ".join(LEAD_FIELDS[1:]))
        for row in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            fields = {k: row[k] for k in LEAD_FIELDS if k in row and k != "company"}
            if not row.get("company") or store.find_duplicate(
                row["company"], fields.get("contact_name", ""), fields.get("email", "")
            ):
                skipped += 1
                continue
            fields.setdefault("source", "csv import")
            store.add_lead(row["company"], **fields)
            added += 1
    print(f"Imported {added} lead(s), skipped {skipped} (duplicates or missing company).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sales-agent", description="A sales agent built on Claude.")
    parser.add_argument("-c", "--config", help="path to config.toml (default: ./config.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a config.toml to fill in")
    p.add_argument("path", nargs="?", default=str(DEFAULT_CONFIG))
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("chat", help="interactive session with the agent")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("ask", help="one request, e.g. ask 'research and qualify Acme Corp'")
    p.add_argument("prompt", nargs="+")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("leads", help="list leads (no API call)")
    p.add_argument("query", nargs="?")
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_leads)

    p = sub.add_parser("show", help="show one lead with activity and drafts")
    p.add_argument("lead_id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("drafts", help="review outreach drafts")
    p.add_argument("--status", default="draft", help="draft (default), sent, discarded, or '' for all")
    p.add_argument("--mark", nargs=2, metavar=("DRAFT_ID", "STATUS"), help="e.g. --mark 3 sent")
    p.set_defaults(func=cmd_drafts)

    p = sub.add_parser("pipeline", help="pipeline summary (no API call)")
    p.add_argument("--stale-days", type=int, default=14)
    p.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("import", help="import leads from a CSV with a 'company' column")
    p.add_argument("csv_file")
    p.set_defaults(func=cmd_import)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
