"""CLI entrypoint for the Flask GitHub App runtime."""

from __future__ import annotations

import argparse
import json
import sys

from werkzeug.serving import WSGIRequestHandler, run_simple

from .app import build_runtime_dependencies, create_app
from .config import ConfigurationError, Settings
from .github import GitHubError
from .service import BotError


class SanitizedRequestHandler(WSGIRequestHandler):
    """Access logger that strips OAuth codes and tokens from query logs."""

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        path = self.path.split("?", 1)[0]
        self.log(
            "info",
            '"%s %s %s" %s %s',
            self.command,
            path,
            self.request_version,
            code,
            size,
        )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.command == "serve":
            app = create_app(settings, start_worker=True)
            runtime = app.extensions.get("runtime")
            try:
                run_simple(
                    hostname=args.host,
                    port=args.port,
                    application=app,
                    use_reloader=False,
                    use_debugger=False,
                    threaded=True,
                    processes=1,
                    request_handler=SanitizedRequestHandler,
                )
            except KeyboardInterrupt:
                return 0
            finally:
                if runtime is not None:
                    runtime.shutdown()
            return 0

        _settings, service, _github, _auth, _verifier = build_runtime_dependencies(
            settings,
        )
        try:
            if args.command == "sync":
                result = service.sync(args.pr, regenerate=args.regenerate)
                service.flush_publications()
                print(json.dumps(_cli_payload(result), sort_keys=True))
                return 0
            if args.command == "flush":
                count = service.flush_publications()
                print(json.dumps({"state": "flushed", "count": count}, sort_keys=True))
                return 0
        finally:
            service.shutdown()
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except BotError as error:
        print(str(error), file=sys.stderr)
        return 1
    except GitHubError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lasthuman.server")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    sync = subparsers.add_parser("sync")
    sync.add_argument("--pr", type=int, required=True)
    sync.add_argument("--regenerate", action="store_true", help="discard the stored questions and generate again")

    subparsers.add_parser("flush")
    return parser


def _cli_payload(result: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "state": result.get("state"),
        "pr": result.get("pr"),
    }
    if "snapshot_id" in result:
        payload["id"] = result["snapshot_id"]
    if "receipt_id" in result:
        payload["id"] = result["receipt_id"]
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
