import argparse
import json
import sys
from pathlib import Path

from .exporter import approved_export, digest, prepare, read_json, read_text, write_new


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Aider session walkthrough export"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Create a local review file")
    prep.add_argument("--repo", type=Path, required=True)
    prep.add_argument("--history", type=Path, required=True)
    prep.add_argument("--plan", type=Path, required=True)
    prep.add_argument(
        "--redact-file",
        type=Path,
        help="One exact literal per line; review all fields after redaction",
    )
    prep.add_argument("--out", type=Path, required=True)
    inspect = commands.add_parser(
        "inspect", help="Print the current payload digest and counts"
    )
    inspect.add_argument("--review", type=Path, required=True)
    export = commands.add_parser(
        "export", help="Acknowledge and export the exact review payload"
    )
    export.add_argument("--review", type=Path, required=True)
    export.add_argument("--ack-sha256", required=True)
    export.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            redactions = (
                read_text(args.redact_file).splitlines() if args.redact_file else []
            )
            review = prepare(args.repo, args.history, read_json(args.plan), redactions)
            write_new(args.out, review)
            print(
                "Review the complete payload, including transcript, titles and patches."
            )
            print("SHA-256:", review["sha256"])
        elif args.command == "inspect":
            review = read_json(args.review)
            payload = review.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("Invalid review payload")
            print(
                json.dumps(
                    dict(
                        sha256=digest(payload),
                        checkpoints=len(payload.get("checkpoints", [])),
                    )
                )
            )
        else:
            result = approved_export(read_json(args.review), args.ack_sha256)
            write_new(args.out, result)
            print("Exported the acknowledged payload.")
    except (OSError, ValueError, UnicodeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
