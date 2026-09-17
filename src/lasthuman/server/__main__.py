"""CLI entrypoint for the Flask GitHub App runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from werkzeug.serving import WSGIRequestHandler, run_simple

from .app import build_runtime_dependencies, create_app
from .config import ConfigurationError, GatewaySettings, Settings, load_settings
from .gateway import GatewayRuntimeError
from .github import GitHubError, GitHubInstallationDiscovery
from .migration import MigrationError, import_legacy_database
from .registration import RegistrationError
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
        settings = load_settings()
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

        if isinstance(settings, GatewaySettings):
            return _gateway_command(args, settings)
        return _fixed_command(args, settings)
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except BotError as error:
        print(str(error), file=sys.stderr)
        return 1
    except GitHubError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (RegistrationError, GatewayRuntimeError, MigrationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except RuntimeError as error:
        if str(error) != "another Last Human gateway already holds the data directory lock":
            raise
        print(str(error), file=sys.stderr)
        return 1


def _fixed_command(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "import-legacy":
        raise ConfigurationError("import-legacy requires TLH_REGISTRATION_MODE=first-event")
    if args.repository_id is not None or args.command == "flush" and args.all_registered:
        raise ConfigurationError("repository selection flags require TLH_REGISTRATION_MODE=first-event")
    _settings, service, _github, _auth, _verifier = build_runtime_dependencies(settings)
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
    return 1


def _gateway_command(args: argparse.Namespace, settings: GatewaySettings) -> int:
    if args.command == "import-legacy":
        result = import_legacy_database(
            settings, source_db=args.source_db, repository_id=args.repository_id, repository=args.repository,
            owner_id=args.owner_id, discovery=GitHubInstallationDiscovery(settings), dry_run=args.dry_run,
        )
        print(json.dumps(result.to_dict(), sort_keys=True))
        return 0
    if args.command == "sync" and args.repository_id is None:
        raise ConfigurationError("first-event sync requires --repository-id/--repo-id")
    if args.command == "flush" and (args.repository_id is not None) == args.all_registered:
        raise ConfigurationError("first-event flush requires either --repository-id/--repo-id or --all-registered")
    app = create_app(settings, start_worker=False)
    manager = app.extensions["tenant_manager"]
    try:
        if args.command == "sync":
            container = manager.resolve(args.repository_id)
            result = container.service.sync(args.pr, regenerate=args.regenerate)
            container.service.flush_publications()
            print(json.dumps(_cli_payload(result), sort_keys=True))
            return 0
        if args.command == "flush":
            repository_ids = (
                [args.repository_id] if args.repository_id is not None
                else [context.repository_id for context in manager.registry.list_registered()]
            )
            repositories: list[dict[str, int]] = []
            for repository_id in repository_ids:
                container = manager.resolve(repository_id)
                count = container.service.flush_publications()
                repositories.append({"repository_id": repository_id, "count": count})
            print(json.dumps({
                "state": "flushed", "count": sum(item["count"] for item in repositories), "repositories": repositories,
            }, sort_keys=True))
            return 0
    finally:
        manager.shutdown()
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
    sync.add_argument("--repository-id", "--repo-id", dest="repository_id", type=_arg_positive_int)

    flush = subparsers.add_parser("flush")
    flush.add_argument("--repository-id", "--repo-id", dest="repository_id", type=_arg_positive_int)
    flush.add_argument("--all-registered", action="store_true")
    legacy = subparsers.add_parser("import-legacy", description="Import a stopped, same-context legacy SQLite Store.")
    legacy.add_argument("--source-db", type=Path, required=True)
    legacy.add_argument("--repository-id", "--repo-id", dest="repository_id", type=_arg_positive_int, required=True)
    legacy.add_argument("--repository", required=True)
    legacy.add_argument("--owner-id", type=_arg_positive_int, required=True)
    legacy.add_argument("--dry-run", action="store_true")
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


def _arg_positive_int(value: str) -> int:
    if not value.isascii() or not value.isdigit() or len(value) > 19 or not 0 < int(value) < (1 << 63):
        raise argparse.ArgumentTypeError("must be a positive integer")
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
