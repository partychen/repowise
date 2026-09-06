from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .common import Error, load_yaml, safe_path
from .core import approval_request, approve, initialize, render_snapshot, validate_config


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="review-memory",
        description="Evidence-backed, human-approved repository review. Advisory and local-only.",
    )
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--root", type=Path, default=Path.cwd(), help="Target repository; defaults to current directory.")
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create local state and empty, untrusted knowledge configuration.")
    init.add_argument("--repository", required=True)
    commands.add_parser("doctor", help="Show runtime dependencies and repository configuration.")
    sync = commands.add_parser("sync", help="Connect a project and resume merged-PR collection and the learning inbox.")
    sync.add_argument("--repository", help="owner/repo; required on first use, saved for later syncs.")
    sync.add_argument("--max-prs", type=int, default=20, help="PR collection budget per batch (1-100).")
    sync.add_argument("--since", help="Optional initial merged-at lower bound; default: all merged history.")
    sync.add_argument("--refresh", action="store_true", help="Start a full feedback refresh after the current queue finishes.")
    sync.add_argument("--fixture", type=Path, help="Use synthetic offline data in an isolated sync state.")
    status = commands.add_parser("status", help="Show project sync progress, pending learning tasks and accumulated candidates.")
    status.add_argument("--offline", action="store_true", help="Inspect the isolated fixture workflow.")
    status.add_argument("--limit", type=int, default=20)
    bootstrap = commands.add_parser("bootstrap", help="Collect a bounded historical window and prepare induction tasks.")
    bootstrap.add_argument("--repository", required=True)
    bootstrap.add_argument("--since", required=True)
    bootstrap.add_argument("--until")
    bootstrap.add_argument("--max-prs", type=int, default=50)
    bootstrap.add_argument("--fixture", type=Path)
    harvest = commands.add_parser("harvest", help="Incrementally collect one merged PR.")
    harvest.add_argument("--repository", required=True)
    harvest.add_argument("--pr", required=True, type=int)
    harvest.add_argument("--fixture", type=Path)
    propose = commands.add_parser("propose", help="Validate host induction results; never approve them.")
    propose.add_argument("--task", type=Path, required=True)
    propose.add_argument("--response", type=Path, required=True)
    approval = commands.add_parser("approval-request", help="Prepare exact content for independent maintainer signing.")
    approval.add_argument("--file", type=Path, required=True)
    approval.add_argument("--kind", choices=("knowledge", "detector"), required=True)
    approval.add_argument("--identity", required=True)
    approval.add_argument("--reason", required=True)
    approved = commands.add_parser("approve", help="Verify a maintainer's detached SSH signature and store approved revision.")
    approved.add_argument("--request", type=Path, required=True)
    approved.add_argument("--signature", type=Path, required=True)
    snapshot = commands.add_parser("snapshot", help="Render approved knowledge from a trusted Git commit.")
    snapshot.add_argument("--trusted-ref", required=True)
    review = commands.add_parser("review", help="Prepare review at immutable Git revisions; never execute project code.")
    for option in ("repository", "base", "head", "trusted-ref"):
        review.add_argument("--" + option, required=True)
    review.add_argument("--max-files", type=int, default=100)
    review.add_argument("--max-bytes", type=int, default=500000)
    review.add_argument("--reference-query", help="Optionally freeze relevant external reference packs into the host task.")
    review.add_argument("--reference-limit", type=int, default=3)
    final = commands.add_parser("finalize", help="Validate host semantic findings against fixed code evidence.")
    final.add_argument("--run-id", required=True)
    final.add_argument("--response", type=Path, required=True)
    final.add_argument("--replay-id", help="Finalize inside this isolated replay instead of production runs.")
    replay = commands.add_parser("replay", help="Prepare isolated temporal replay tasks; no answer data is read.")
    replay.add_argument("--dataset", type=Path, required=True)
    score = commands.add_parser("score", help="Score frozen replay outputs against independent human adjudication.")
    score.add_argument("--replay-id", required=True)
    score.add_argument("--labels", type=Path, required=True)
    packs = commands.add_parser("packs", help="List or select advisory reference packs, not approved policy.")
    pack_commands = packs.add_subparsers(dest="pack_command", required=True)
    pack_commands.add_parser("list")
    pack_commands.add_parser("sources", help="Show pinned upstream mappings, synthesis scope and exclusions.")
    selection = pack_commands.add_parser("select")
    selection.add_argument("--query", required=True)
    selection.add_argument("--limit", type=int, default=3)
    return result


def dispatch(args):
    root = args.root.resolve()
    if not root.is_dir():
        raise Error("Target root must be an existing directory.")
    if getattr(args, "repository", None) is not None:
        args.repository = args.repository.lower()
    if args.command == "init":
        return initialize(root, args.repository)
    if args.command == "sync":
        from .sync import sync_project
        return sync_project(root, args.repository, max_prs=args.max_prs, since=args.since,
                            refresh=args.refresh, fixture=args.fixture)
    if args.command == "doctor":
        configuration = safe_path(root, ".review/config.yaml")
        result = {
            "version": __version__, "python": sys.version.split()[0],
            "tools": {tool: shutil.which(tool) for tool in ("git", "gh", "ssh-keygen")},
            "initialized": configuration.exists(), "root": str(root),
            "limitations": ["local reports only", "no project execution", "host model costs may be unknown"],
        }
        if configuration.exists():
            config = load_yaml(configuration)
            validate_config(config)
            result["repository"] = config["repository"]
        return result
    if args.command == "packs":
        from .packs import list_packs, select_packs, source_inventory
        if args.pack_command == "list":
            return {"packs": [{key: pack[key] for key in ("id", "title", "tags", "authority")} for pack in list_packs()]}
        if args.pack_command == "sources":
            return source_inventory()
        return select_packs(args.query, args.limit)
    config = load_yaml(safe_path(root, ".review/config.yaml"))
    validate_config(config)
    if getattr(args, "repository", config["repository"]) != config["repository"]:
        raise Error("Command repository does not match initialized repository.")
    if args.command == "bootstrap":
        from .collect import bootstrap
        return bootstrap(root, args.repository, args.since, args.until, args.max_prs, args.fixture)
    if args.command == "harvest":
        from .collect import harvest
        return harvest(root, args.repository, args.pr, args.fixture)
    if args.command == "propose":
        from .propose import propose
        return propose(root, args.task, args.response)
    if args.command == "status":
        from .sync import project_status
        return project_status(root, offline=args.offline, limit=args.limit)
    if args.command == "approval-request":
        return approval_request(root, args.file, args.kind, args.identity, args.reason)
    if args.command == "approve":
        return approve(root, args.request, args.signature)
    if args.command == "snapshot":
        return render_snapshot(root, args.trusted_ref)
    if args.command == "review":
        from .review import prepare_review
        return prepare_review(root, args.repository, args.base, args.head, args.trusted_ref, args.max_files, args.max_bytes,
                              reference_query=args.reference_query, reference_limit=args.reference_limit)
    if args.command == "finalize":
        from .review import finalize_review
        if args.replay_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", args.replay_id):
                raise Error("Invalid replay ID.")
            runs_root = safe_path(root, f".review/local/evaluation/{args.replay_id}/runs")
            return finalize_review(root, args.run_id, args.response, runs_root=runs_root)
        return finalize_review(root, args.run_id, args.response)
    if args.command == "replay":
        from .replay import prepare_replay
        return prepare_replay(root, args.dataset)
    if args.command == "score":
        from .replay import score_replay
        return score_replay(root, args.replay_id, args.labels)
    raise Error(f"Unsupported command: {args.command}")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
        if isinstance(result, dict) and (result.get("complete") is False or
                                        result.get("status") in {"incomplete", "failed", "partial", "budget_exhausted"}):
            return 2
        return 0
    except (Error, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
