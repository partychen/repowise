from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .common import Error, safe_path
from .core import approval_request, approve, render_snapshot
from .storage import initialize_project, project_config, project_storage


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="repowise",
        description="Accumulate project knowledge, review PRs, and guide authorized host feature implementation.",
    )
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--root", type=Path, default=Path.cwd(), help="Target repository; defaults to current directory.")
    result.add_argument("--data-home", type=Path,
                        help="External project-data parent directory; defaults to REPOWISE_HOME "
                             "or ~/.repowise/projects. Never inside the target or Skill.")
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create external project memory without modifying the target repository.")
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
    queue = commands.add_parser("approval-queue", help="Show evidence-bound candidates and a readable approval handoff.")
    queue.add_argument("--limit", type=int, default=20)
    queue.add_argument("--offset", type=int, default=0)
    prepare = commands.add_parser("prepare-approval", help="Prepare an unsigned knowledge request for maintainer review.")
    prepare.add_argument("--candidate", type=Path, required=True)
    prepare.add_argument("--identity", required=True)
    prepare.add_argument("--owner", required=True)
    prepare.add_argument("--reason", required=True)
    prepare.add_argument("--effective-from")
    prepare.add_argument("--revision", type=int)
    approval = commands.add_parser("approval-request", help="Prepare exact content for independent maintainer signing.")
    approval.add_argument("--file", type=Path, required=True)
    approval.add_argument("--kind", choices=("knowledge", "detector"), required=True)
    approval.add_argument("--identity", required=True)
    approval.add_argument("--reason", required=True)
    approved = commands.add_parser("approve", help="Verify a maintainer's detached SSH signature and store approved revision.")
    approved.add_argument("--request", type=Path, required=True)
    approved.add_argument("--signature", type=Path, required=True)
    snapshot = commands.add_parser("snapshot", help="Render approved knowledge from the external policy Git repository.")
    snapshot.add_argument("--trusted-ref", required=True, help="Trusted commit in this project's external memory repository.")
    review = commands.add_parser("review", help="Prepare review at immutable Git revisions; never execute project code.")
    review.add_argument("--repository", help="Project repository; defaults to the saved binding.")
    review.add_argument("--pr", help="GitHub PR number or URL; resolves immutable local revisions without checkout/fetch.")
    review.add_argument("--base", help="Explicit base revision when not using --pr.")
    review.add_argument("--head", help="Explicit head revision when not using --pr.")
    review.add_argument("--trusted-ref", required=True, help="Trusted commit in the external memory/policy repository, not PR head.")
    review.add_argument("--max-files", type=int, default=100)
    review.add_argument("--max-bytes", type=int, default=500000)
    review.add_argument("--context-path", action="append", default=[],
                        type=lambda value: value.replace("\\", "/"),
                        help="Additional repository-relative file to freeze at base/head; repeatable, "
                             "within the same file/byte budget. Never reads the worktree.")
    review.add_argument("--reference-query", help="Optionally freeze relevant external reference packs into the host task.")
    review.add_argument("--reference-limit", type=int, default=3)
    from .review_agents import ROLES
    review.add_argument("--review-mode", choices=("auto", "single", "multi"), default="auto",
                        help="Freeze an explainable host dispatch plan; never launch workers in the CLI.")
    review.add_argument("--review-role", choices=tuple(ROLES), action="append", default=[],
                        help="Select a host review role; repeatable, incompatible with single mode.")
    review.add_argument("--max-review-workers", type=int, default=3,
                        help="Host concurrency bound, 1-6; serial fallback must be recorded honestly.")
    feature = commands.add_parser("feature", help="Prepare project context for user-authorized host feature implementation.")
    feature.add_argument("--repository", help="Project repository; defaults to the saved binding.")
    feature.add_argument("--goal", required=True, help="The actual user-requested feature and acceptance criteria.")
    feature.add_argument("--base", default="HEAD", help="Immutable code baseline; defaults to current HEAD.")
    feature.add_argument("--trusted-ref", help="Selected external policy commit; missing policy remains a visible gap.")
    feature.add_argument("--context-path", action="append", required=True,
                         type=lambda value: value.replace("\\", "/"),
                         help="Relevant existing repository file selected by the host; repeatable.")
    feature.add_argument("--max-files", type=int, default=100)
    feature.add_argument("--max-bytes", type=int, default=500000)
    finish = commands.add_parser("feature-finish", help="Record actual host implementation/check results; execute nothing.")
    finish.add_argument("--feature-id", required=True)
    finish.add_argument("--response", type=Path, required=True)
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
    target = args.root.expanduser().resolve()
    if not target.is_dir():
        raise Error("Target root must be an existing directory.")
    if getattr(args, "repository", None) is not None:
        args.repository = args.repository.lower()
    if args.command == "packs":
        from .packs import list_packs, select_packs, source_inventory
        if args.pack_command == "list":
            return {"packs": [{key: pack[key] for key in ("id", "title", "tags", "authority")} for pack in list_packs()]}
        if args.pack_command == "sources":
            return source_inventory()
        return select_packs(args.query, args.limit)
    storage = project_storage(target, args.data_home)
    result = _dispatch_project(args, storage)
    return {**result, **storage.describe()}


def _dispatch_project(args, storage):
    root = storage.storage_root
    if args.command == "init":
        return initialize_project(storage, args.repository)
    if args.command == "sync":
        from .sync import sync_project
        if safe_path(root, ".review/config.yaml").exists():
            project_config(storage)
        else:
            if args.repository is None:
                raise Error("First sync requires --repository owner/repo; project memory is stored externally.")
            initialize_project(storage, args.repository)
        result = sync_project(root, args.repository, max_prs=args.max_prs, since=args.since,
                              refresh=args.refresh, fixture=args.fixture)
        return _learning_actions(result, storage, offline=args.fixture is not None)
    if args.command == "doctor":
        configuration = safe_path(root, ".review/config.yaml")
        result = {
            "version": __version__, "python": sys.version.split()[0],
            "tools": {tool: shutil.which(tool) for tool in ("git", "gh", "ssh-keygen")},
            "initialized": configuration.exists(), "root": str(storage.target_root),
            "limitations": ["external local reports only", "no target writes or execution", "host model costs may be unknown"],
        }
        if configuration.exists():
            config = project_config(storage)
            result["repository"] = config["repository"]
        return result
    config = project_config(storage)
    if args.command in {"review", "feature"} and args.repository is None:
        args.repository = config["repository"]
    if getattr(args, "repository", config["repository"]) != config["repository"]:
        raise Error("Command repository does not match initialized repository.")
    if args.command == "bootstrap":
        from .collect import bootstrap
        from .sync import project_status
        result = bootstrap(root, args.repository, args.since, args.until, args.max_prs, args.fixture)
        progress = project_status(root, offline=args.fixture is not None)
        return _learning_actions({**progress, **result}, storage, offline=args.fixture is not None)
    if args.command == "harvest":
        from .collect import harvest
        from .sync import project_status
        result = harvest(root, args.repository, args.pr, args.fixture)
        progress = project_status(root, offline=args.fixture is not None)
        return _learning_actions({**progress, **result}, storage, offline=args.fixture is not None)
    if args.command == "propose":
        from .propose import propose
        return propose(root, args.task, args.response)
    if args.command == "status":
        from .sync import project_status
        return _learning_actions(project_status(root, offline=args.offline, limit=args.limit),
                                 storage, offline=args.offline)
    if args.command == "approval-queue":
        from .approvals import approval_queue
        return approval_queue(root, limit=args.limit, offset=args.offset,
                              target_root=storage.target_root, data_home=storage.data_home)
    if args.command == "prepare-approval":
        from .approvals import prepare_approval
        return prepare_approval(root, args.candidate, args.identity, args.owner, args.reason,
                                effective_from=args.effective_from, revision=args.revision,
                                target_root=storage.target_root, data_home=storage.data_home)
    if args.command == "approval-request":
        return approval_request(root, args.file, args.kind, args.identity, args.reason)
    if args.command == "approve":
        return approve(root, args.request, args.signature)
    if args.command == "snapshot":
        return render_snapshot(root, args.trusted_ref)
    if args.command == "review":
        from .review import prepare_review
        if args.pr is not None:
            if args.base is not None or args.head is not None:
                raise Error("Choose --pr or explicit --base/--head, not both.")
            from .pull_requests import prepare_pull_request_review
            return prepare_pull_request_review(
                storage.target_root, args.repository, args.pr, args.trusted_ref, memory_root=root,
                max_files=args.max_files, max_bytes=args.max_bytes, context_paths=args.context_path,
                reference_query=args.reference_query, reference_limit=args.reference_limit,
                review_mode=args.review_mode, review_roles=args.review_role,
                max_review_workers=args.max_review_workers,
            )
        if args.base is None or args.head is None:
            raise Error("Review requires --pr or both --base and --head.")
        return prepare_review(storage.target_root, args.repository, args.base, args.head,
                              args.trusted_ref, args.max_files, args.max_bytes, memory_root=root,
                              reference_query=args.reference_query, reference_limit=args.reference_limit,
                              context_paths=args.context_path, review_mode=args.review_mode,
                              review_roles=args.review_role, max_review_workers=args.max_review_workers)
    if args.command == "feature":
        from .feature import prepare_feature
        return prepare_feature(
            storage.target_root, args.repository, args.goal, memory_root=root,
            base=args.base, trusted_ref=args.trusted_ref, context_paths=args.context_path,
            max_files=args.max_files, max_bytes=args.max_bytes,
        )
    if args.command == "feature-finish":
        from .feature import finish_feature
        return finish_feature(root, args.feature_id, args.response)
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
        return prepare_replay(storage.target_root, args.dataset, memory_root=root)
    if args.command == "score":
        from .replay import score_replay
        return score_replay(root, args.replay_id, args.labels)
    raise Error(f"Unsupported command: {args.command}")


def _learning_actions(result, storage, *, offline=False):
    if result.get("learning_gap_count", 0):
        result = {**result, "status": "partial"}
    actions = []
    if not offline and not result.get("pending_task_count") and result.get("remaining_pr_count", 0):
        actions.append({"action": "continue_sync", "arguments": storage.arguments("sync")})
    if result.get("knowledge_count", 0):
        actions.append({
            "action": "review_candidates", "arguments": storage.arguments("approval-queue"),
            "blocks_learning": False,
            "note": "Read the candidate handoff and select knowledge before preparing a maintainer signing request.",
        })
    return {**result, "next_actions": actions}


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
