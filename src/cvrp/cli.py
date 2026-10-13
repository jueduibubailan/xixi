"""cvrp command line.

    uv run cvrp start task.yaml            # run the whole pipeline
    uv run cvrp ui                         # dashboard at http://127.0.0.1:8420
    uv run cvrp new-problem my-variant     # scaffold a new VRP-variant example
    uv run cvrp problems                   # list the variants this project can build
    uv run cvrp status                     # one-screen run summary
    uv run cvrp log [-n 100]               # tail the pipeline log
    uv run cvrp show <path>                # print an artifact
    uv run cvrp grader [--json]            # re-run the exit gate
    uv run cvrp instances [--force]        # (re)generate the problem's data
    uv run cvrp validate                   # config + problem + prompt check
    uv run cvrp clean [--all]              # drop run directories
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from cvrp import __version__
from cvrp.compat.coral_adapter import coral_info
from cvrp.config import ConfigError, load_config, project_root, resolve_path, task_slug

DEFAULT_TASK = "task.yaml"
DEFAULT_UI_PORT = 8420

#: Stages the pipeline runs, in order. Kept as a literal here so building the
#: argument parser does not import the orchestrator (and through it the LLM
#: backends) just to print `--help`. `cvrp stage` validates against the
#: authoritative list in `cvrp.pipeline.orchestrator.REPLAYABLE_STAGES`.
STAGE_CHOICES = (
    "retrieval",
    "analysis",
    "code_generation",
    "code_error",
    "code_revise",
    "feasibility",
    "update",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _load(args: argparse.Namespace) -> dict[str, Any]:
    return load_config(
        args.task,
        defaults_path=getattr(args, "defaults", "configs/default.yaml"),
        overrides=list(getattr(args, "overrides", []) or []),
        cwd=getattr(args, "cwd", None),
    )


def _results_dir(config: dict[str, Any], override: str | None = None) -> Path:
    raw = override or str((config.get("workspace") or {}).get("results_dir") or "./results")
    path = Path(raw)
    return path if path.is_absolute() else project_root(config) / path


def _resolve_run(config: dict[str, Any], run: str | None) -> Path | None:
    from cvrp.run.run_dir import latest_run_dir

    if run:
        candidate = Path(run)
        if candidate.is_dir():
            return candidate
        guess = _results_dir(config) / task_slug(config) / run
        if guess.is_dir():
            return guess
        return None
    return latest_run_dir(_results_dir(config), task_slug(config))


def _print_stage_table(config: dict[str, Any], run_dir: Path) -> None:
    from cvrp.run.run_dir import read_json

    state = read_json(run_dir / "state.json", default={}) or {}
    records = state.get("stages") or {}
    print(f"run      : {run_dir}")
    print(
        f"problem  : {(config.get('problem') or {}).get('slug')}  "
        f"(task: {(config.get('task') or {}).get('name')})"
    )
    print(f"status   : {state.get('status')}   round: {state.get('round')}")
    print(f"best cost: {state.get('best_cost')}   initial: {state.get('initial_cost')}")
    print()
    print(f"{'stage':<18}{'kind':<10}{'status':<10}{'try':<5}{'dur':<10}message")
    print("-" * 96)
    for stage in config.get("stages", []):
        record = records.get(stage["name"]) or {}
        print(
            f"{stage['name']:<18}{stage.get('kind', 'agent'):<10}"
            f"{str(record.get('status', 'pending')):<10}{str(record.get('attempts', 0)):<5}"
            f"{str(record.get('duration_s', '-')):<10}{str(record.get('message', ''))[:44]}"
        )


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_start(args: argparse.Namespace) -> int:
    from cvrp.pipeline.orchestrator import start

    config = _load(args)
    result = start(config, results_dir=_results_dir(config, args.results_dir), run_id=args.run_id)
    print()
    print(f"run dir : {result['run_dir']}")
    print(f"result  : {'SUCCESS' if result['success'] else 'FAILED'} {result['reason']}")
    print(f"report  : {result['report']}")
    return 0 if result["success"] else 1


def cmd_ui(args: argparse.Namespace) -> int:
    import uvicorn

    from cvrp.web.app import create_app

    config = _load(args)
    app = create_app(config, project_root(config), results_dir=_results_dir(config, args.results_dir))
    url = f"http://{args.host}:{args.port}"
    print(f"cvrp dashboard: {url}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    run_dir = _resolve_run(config, args.run)
    if run_dir is None:
        print("no run yet - start one with `uv run cvrp start task.yaml`")
        return 1
    _print_stage_table(config, run_dir)
    print()
    print(f"CORAL shell: {coral_info().describe()}")
    return 0


def cmd_stages(args: argparse.Namespace) -> int:
    return cmd_status(args)


def cmd_stage(args: argparse.Namespace) -> int:
    """Re-run exactly one stage against an existing run (the debugging entry point).

    This subcommand deliberately does *not* reuse the shared positional ``task``
    slot: you address it by stage and run id, and it owns its own ``--task`` flag.
    Mixing the two produces calls like ``cvrp stage analysis my-task.yaml``, where
    the second positional silently means "task file", which is the kind of trap
    that wastes an afternoon.
    """
    from cvrp.pipeline.orchestrator import REPLAYABLE_STAGES, replay_stage

    stage = args.stage or args.stage_flag
    if stage not in REPLAYABLE_STAGES:
        print(f"unknown stage {stage!r}; expected one of: {', '.join(REPLAYABLE_STAGES)}")
        return 2
    config = load_config(
        args.task,
        defaults_path=args.defaults,
        overrides=list(args.overrides or []),
        cwd=getattr(args, "cwd", None),
    )
    run_dir = _resolve_run(config, args.run)
    if run_dir is None:
        print("no run found - start one with `uv run cvrp start task.yaml`")
        return 1
    result = replay_stage(config, run_dir, stage, results_dir=_results_dir(config, args.results_dir))
    print()
    print(f"run    : {result['run_dir']}")
    print(f"stage  : {result['stage']}  ->  {'PASSED' if result['passed'] else 'FAILED'}")
    print(f"summary: {result['summary']}")
    for issue in result["issues"]:
        print(f"issue  : {issue}")
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    return 0 if result["passed"] else 1


def cmd_problems(args: argparse.Namespace) -> int:
    """List the variants this project can build."""
    from cvrp.problems import ProblemError, find_problem_dir, list_problems, load_problem

    try:
        config: dict[str, Any] = _load(args)
    except ConfigError:
        config = {"_project_root": str(Path.cwd())}

    slugs = list_problems(config)
    if not slugs:
        print("no problem directories found; create one with `cvrp new-problem <slug>`")
        return 1
    print(f"{'slug':<20}{'name':<44}{'generated dir':<24}prompts")
    print("-" * 100)
    for slug in slugs:
        try:
            root = find_problem_dir(slug, config)
            plugin = load_problem(root, slug=slug)
        except ProblemError as exc:
            print(f"{slug:<20}!! {exc}")
            continue
        prompts = plugin.spec.get("prompts") or {}
        gen_dir = (plugin.spec.get("generated") or {}).get("dir", "artifacts/generated")
        print(f"{slug:<20}{plugin.name[:42]:<44}{str(gen_dir):<24}{len(prompts)} declared")
    return 0


def cmd_new_problem(args: argparse.Namespace) -> int:
    """Scaffold a new VRP-variant example under problems/<slug>/."""
    from cvrp.problems.scaffold import scaffold_problem

    root = Path(args.project).expanduser().resolve() if args.project else Path.cwd()
    target = Path(args.dir).expanduser() if args.dir else root / "problems" / args.slug
    if target.exists() and not args.force:
        print(f"refusing to overwrite existing directory: {target} (pass --force)")
        return 1
    written = scaffold_problem(
        slug=args.slug,
        name=args.name or args.slug,
        target=target,
        description=args.description or "",
        variant_label=args.variant_label or "",
        baseline=args.baseline,
        force=args.force,
    )
    print(f"scaffolded {target}")
    for path in written:
        print(f"  {path.relative_to(target)}")
    print()
    print("next:")
    print(f"  1. edit {target / 'prompts' / 's1_analysis.md'} and s2_codegen.md for your variant")
    print(f"  2. implement verify()/build_dataset() in {target / 'checks.py'}")
    print(f"  3. point task.yaml at it:  problem: {{slug: {args.slug}}}")
    print("  4. uv run cvrp validate && uv run cvrp instances && uv run cvrp start task.yaml")
    return 0


def cmd_instances(args: argparse.Namespace) -> int:
    config = _load(args)
    problem = config["_problem"]
    out_dir = resolve_path(config, args.out or problem.data_dir_name)
    if args.force and out_dir.is_dir():
        shutil.rmtree(out_dir, ignore_errors=True)
    if problem.data_ready(out_dir) and not args.force:
        print(f"data already present under {out_dir} (use --force to regenerate)")
        return 0
    if not problem.data_generator and problem.checks_module is None:
        print(f"problem {problem.slug!r} declares no data generator; nothing to do")
        return 0
    written = problem.build_dataset(out_dir, force=args.force)
    if not written:
        print(f"nothing written (the generator reported no new items) under {out_dir}")
        return 0
    for item in written:
        name = item.get("name") if isinstance(item, dict) else item
        print(f"wrote {name}")
    print(f"output: {out_dir}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    from cvrp.run.run_dir import read_text

    config = _load(args)
    run_dir = _resolve_run(config, args.run)
    if run_dir is None:
        print("no run found")
        return 1
    name = args.name or "pipeline.log"
    text = read_text(run_dir / "logs" / name, max_chars=5_000_000)
    lines = text.splitlines()
    if args.number and args.number > 0:
        lines = lines[-args.number :]
    print("\n".join(lines))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    config = _load(args)
    run_dir = _resolve_run(config, args.run)
    if run_dir is None:
        print("no run found")
        return 1
    target = Path(args.path)
    if not target.is_absolute():
        target = run_dir / target
    if not target.is_file():
        print(f"no such artifact: {target}")
        return 1
    print(target.read_text(encoding="utf-8", errors="replace"))
    return 0


def cmd_grader(args: argparse.Namespace) -> int:
    from cvrp.executor.grader import grade_run
    from cvrp.run.run_dir import write_json

    config = _load(args)
    run_dir = _resolve_run(config, args.run)
    if run_dir is None:
        print("no run found")
        return 1
    bundle = grade_run(config, run_dir, run_dir / "worktrees" / "code_error", problem=config["_problem"])
    write_json(run_dir / "artifacts" / "runs" / "grader_result.json", bundle)
    if args.json:
        print(json.dumps(bundle, indent=2))
    else:
        print(f"problem  : {bundle.get('problem')}")
        print(f"score    : {bundle['score']}")
        print(f"feasible : {bundle['feasible']}")
        print(f"best_cost: {bundle['best_cost']}")
        reason = (bundle.get("details") or {}).get("reason")
        if reason:
            print(f"reason   : {reason}")
    return 0 if bundle["score"] > 0 else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the runtime prerequisites a real run needs, before spending a turn.

    The most expensive failure mode on a fresh machine is discovering three stages
    in that the agent CLI was never on the path of the process that `uv run` spawns.
    This command answers that in one shot.
    """
    from cvrp.llm.claude_code import _clean_env, resolve_claude_binary, routing_summary

    config = _load(args)
    problem = config["_problem"]
    llm = dict(config.get("llm") or {})
    backend = str(llm.get("backend") or "claude_code")
    ok = True

    print(f"task     : {(config.get('task') or {}).get('name')}")
    print(f"project  : {project_root(config)}")
    print(f"problem  : {problem.slug}  ({problem.root})")
    print(f"model    : {llm.get('model')} via {backend}")
    print()

    # 1. the agent CLI and where its traffic goes
    if backend in ("mock", "offline", "dry-run"):
        print("agent CLI: not needed (backend is the offline stub)")
    else:
        binary = str(llm.get("claude_bin") or "claude")
        env = _clean_env(dict(llm.get("env") or {}), inherit_routing=bool(llm.get("inherit_routing_env", True)))
        routing = routing_summary(env)
        try:
            resolved = resolve_claude_binary(binary)
            print(f"agent CLI: OK  {resolved}")
        except FileNotFoundError as exc:
            ok = False
            print(f"agent CLI: MISSING - {exc}")
            print("           every agent stage will fail with rc=127, and the gates will")
            print("           report that no output was produced. Fix it with one of:")
            print("             - install the CLI and make sure it is on PATH for this shell")
            print("             - uv run cvrp start task.yaml llm.claude_bin=<absolute path>")
            print("             - uv run cvrp start task.yaml llm.backend=mock   (offline)")
        print(f"routing  : {routing}")
        if not env.get("ANTHROPIC_BASE_URL") and llm.get("inherit_routing_env", True) is False:
            ok = False
            print("           llm.inherit_routing_env is false, so ANTHROPIC_BASE_URL was removed.")
            print("           With a non-Anthropic model name (such as deepseek-flash) the CLI will")
            print("           then hit api.anthropic.com and fail with 'Failed to authenticate ... 403'.")
        if str(llm.get("model") or "") and not env.get("ANTHROPIC_BASE_URL"):
            print("           note: a non-native model name only works when a router is configured")
            print("           (ANTHROPIC_BASE_URL). If your runs fail with 403/unrecognized_model,")
            print("           export ANTHROPIC_BASE_URL or set it under llm.env in the task config.")

        # Optional live probe: the only check that distinguishes "the router is up
        # and serves this model" from "the messages endpoint is unreachable".
        if getattr(args, "probe", False):
            from cvrp.llm.probe import print_probe

            probe_ok, text = print_probe(env, str(llm.get("model") or ""), timeout=float(args.probe_timeout))
            print(text)
            if not probe_ok:
                ok = False

    # 2. the data the executor runs on
    data_dir = resolve_path(config, problem.data_dir_name)
    if problem.data_ready(data_dir):
        print(f"data     : OK  {data_dir}")
    else:
        ok = False
        print(f"data     : MISSING - expected a marker under {data_dir}")
        print("           fix: uv run cvrp instances")

    # 3. the baseline the agents must modify
    from cvrp.run.worktree import _baseline_dir

    baseline = _baseline_dir(problem)
    if baseline is None:
        print("baseline : none declared (the problem is expected to bring its own code)")
    elif baseline.is_dir():
        entries = sorted(item.name for item in baseline.iterdir())
        print(f"baseline : OK  {baseline}  [{', '.join(entries[:6])}]")
    else:
        ok = False
        print(f"baseline : MISSING - resources.baseline_dir resolved to {baseline}")

    # 4. the prompts the agents will be given
    prompts = problem.spec.get("prompts") or {}
    if not prompts:
        ok = False
        print("prompts  : MISSING - the problem declares none, so the agents would be told nothing")
    else:
        missing = [key for key in prompts if not Path(problem.prompt(key)).is_file()]
        if missing:
            ok = False
            print(f"prompts  : MISSING for {', '.join(missing)}")
        else:
            print(f"prompts  : OK  {len(prompts)} declared, all present")

    # 5. the generated-package expectations, so the contract is runnable
    print(f"program  : {problem.program_relpath}   (instance flag {problem.cli.get('path_arg') or 'none'})")
    print(f"expected : {', '.join(problem.spec.get('program', {}).get('expected_lines') or ['(none declared)'])}")
    print()

    if ok:
        print("all set - `uv run cvrp start task.yaml` should reach a graded result")
    else:
        print("not ready - fix the items marked MISSING above")
    return 0 if ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Static check: config, problem spec, referenced files, generated data."""
    try:
        config = _load(args)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 1

    problem = config["_problem"]
    problems: list[str] = []
    warnings: list[str] = []
    root = project_root(config)

    for stage in config.get("stages", []):
        for key in ("role", "prompt", "judge_prompt", "analyse_prompt", "schema"):
            value = stage.get(key)
            if not value:
                continue
            path = resolve_path(config, value)
            if path.is_file():
                continue
            if key == "role":
                warnings.append(f"{stage['name']}.{key}: missing {path}")
            elif key == "prompt":
                # Falling back to the problem's own prompt is the normal case.
                if not (problem.spec.get("prompts") or {}):
                    problems.append(f"{stage['name']}: no prompt resolved and the problem declares none")
            else:
                warnings.append(f"{stage['name']}.{key}: missing {path}")

    declared = problem.spec.get("prompts") or {}
    if not declared:
        problems.append(f"problem {problem.slug!r} declares no prompts; agents would be told nothing")
    for key, raw in declared.items():
        path = Path(str(raw))
        if not path.is_absolute():
            path = problem.root / path
        if not path.is_file():
            problems.append(f"problem.prompts.{key}: missing {path}")

    if problem.checks_module is None:
        warnings.append(
            "problem has no checks.py: feasibility cannot be verified for this variant "
            "(the engine will only confirm that a solution dump is valid JSON)"
        )
    else:
        if not callable(getattr(problem.checks_module, "verify", None)):
            warnings.append("checks.py declares no verify(): results cannot be independently checked")
        if not callable(getattr(problem.checks_module, "build_dataset", None)) and not problem.data_generator:
            warnings.append("no build_dataset hook and no data.generator: `cvrp instances` will do nothing")

    data_dir = resolve_path(config, problem.data_dir_name)
    if not problem.data_ready(data_dir):
        warnings.append(f"problem data not generated yet under {data_dir}")

    resources = problem.spec.get("resources") or {}
    raw_baseline = str(resources.get("baseline_dir") or "")
    if raw_baseline:
        baseline = Path(raw_baseline)
        if not baseline.is_absolute():
            baseline = root / baseline
        if not baseline.is_dir():
            warnings.append(f"resources.baseline_dir does not exist: {baseline}")
        else:
            for name in resources.get("baseline_subdirs") or []:
                if not (baseline / str(name)).exists():
                    warnings.append(f"resources.baseline_subdirs entry missing: {baseline / str(name)}")

    path_flag = problem.cli.get("path_arg")
    print(f"task       : {(config.get('task') or {}).get('name')}  (slug: {task_slug(config)})")
    print(f"project    : {root}")
    print(f"problem    : {problem.slug}  -  {problem.name}")
    print(f"problem dir: {problem.root}")
    print(f"stages     : {', '.join(s['name'] for s in config.get('stages', []))}")
    print(f"program    : {problem.program_relpath}   (instance flag: {path_flag or 'none'})")
    print(f"data       : {problem.data_dir_name}   (glob: {problem.instance_glob})")
    print(f"scoring    : {problem.score_kind} ({problem.direction})")
    print(f"model      : {(config.get('llm') or {}).get('model')} via {(config.get('llm') or {}).get('backend')}")
    print(f"CORAL      : {coral_info().describe()}")
    for warning in warnings:
        print(f"warning    : {warning}")
    for item in problems:
        print(f"ERROR      : {item}")
    if problems:
        return 1
    print("ok")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    config = _load(args)
    results = _results_dir(config, args.results_dir)
    task_dir = results / task_slug(config)
    if not task_dir.is_dir():
        print(f"nothing to clean under {task_dir}")
        return 0
    if not args.all:
        print(f"would remove {task_dir} (pass --all to actually delete)")
        return 0
    shutil.rmtree(task_dir, ignore_errors=True)
    print(f"removed {task_dir}")
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print(f"cvrp {__version__}")
    print(coral_info().describe())
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cvrp",
        description=(
            "VRP-variant code synthesis: analysis -> codegen -> error -> revise, "
            "driven by problems/<slug>/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  uv run cvrp start task.yaml\n"
            "  uv run cvrp start task.yaml llm.backend=mock run.verbose=true\n"
            "  uv run cvrp new-problem cvrptw-dr --name 'CVRPTW with drones'\n"
            "  uv run cvrp problems\n"
            "  uv run cvrp ui --port 8420\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"cvrp {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("task", nargs="?", default=DEFAULT_TASK, help="path to task.yaml")
        p.add_argument("--defaults", default="configs/default.yaml", help="global defaults file")
        p.add_argument("--results-dir", default=None, help="override workspace.results_dir")
        p.add_argument("--run", default=None, help="run id or run directory (default: latest)")

    p_start = sub.add_parser("start", help="run the pipeline")
    add_common(p_start)
    p_start.add_argument("--run-id", default=None, help="explicit run id (timestamp by default)")
    p_start.add_argument("overrides", nargs="*", help="key=value dotlist overrides")
    p_start.set_defaults(func=cmd_start)

    p_ui = sub.add_parser("ui", help="serve the dashboard")
    add_common(p_ui)
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    p_ui.add_argument("overrides", nargs="*", help="key=value dotlist overrides")
    p_ui.set_defaults(func=cmd_ui)

    p_status = sub.add_parser("status", help="show the latest run summary")
    add_common(p_status)
    p_status.add_argument("overrides", nargs="*")
    p_status.set_defaults(func=cmd_status)

    p_stages = sub.add_parser("stages", help="show the stage table")
    add_common(p_stages)
    p_stages.add_argument("overrides", nargs="*")
    p_stages.set_defaults(func=cmd_stages)

    p_stage = sub.add_parser(
        "stage",
        help="re-run ONE stage of an existing run (debugging; no export, no new report)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  uv run cvrp stage analysis                     # replay S1 on the latest run\n"
            "  uv run cvrp stage code_error --run 20261006-191954\n"
            "  uv run cvrp stage analysis --task configs/tasks/cvrptw.yaml\n"
        ),
    )
    p_stage.add_argument("stage", nargs="?", choices=list(STAGE_CHOICES), help="which stage to replay")
    p_stage.add_argument("--stage", dest="stage_flag", choices=list(STAGE_CHOICES), default=None)
    p_stage.add_argument("--task", default=DEFAULT_TASK, help="path to task.yaml")
    p_stage.add_argument("--defaults", default="configs/default.yaml", help="global defaults file")
    p_stage.add_argument("--results-dir", default=None, help="override workspace.results_dir")
    p_stage.add_argument("--run", default=None, help="run id or run directory (default: latest)")
    p_stage.add_argument("overrides", nargs="*", help="key=value dotlist overrides")
    p_stage.set_defaults(func=cmd_stage)

    p_problems = sub.add_parser("problems", help="list the VRP variants this project can build")
    add_common(p_problems)
    p_problems.add_argument("overrides", nargs="*")
    p_problems.set_defaults(func=cmd_problems)

    p_new = sub.add_parser("new-problem", help="scaffold a new variant under problems/<slug>/")
    p_new.add_argument("slug", help="short identifier, e.g. cvrptw-dr")
    p_new.add_argument("--name", default=None, help="human-readable variant name")
    p_new.add_argument("--variant-label", default=None, help="label shown to the agents")
    p_new.add_argument("--from", dest="description", default=None, help="description of the variant")
    p_new.add_argument("--dir", default=None, help="where to create it (default: <cwd>/problems/<slug>)")
    p_new.add_argument("--project", default=None, help="project root (default: cwd)")
    p_new.add_argument("--baseline", default=None, help="baseline directory to point the spec at")
    p_new.add_argument("--force", action="store_true", help="overwrite an existing directory")
    p_new.set_defaults(func=cmd_new_problem)

    p_log = sub.add_parser("log", help="tail a run log")
    add_common(p_log)
    p_log.add_argument("-n", "--number", type=int, default=200, help="last N lines (0 = all)")
    p_log.add_argument("--name", default="pipeline.log", help="log file name under logs/")
    p_log.add_argument("overrides", nargs="*")
    p_log.set_defaults(func=cmd_log)

    p_show = sub.add_parser("show", help="print an artifact from the latest run")
    add_common(p_show)
    p_show.add_argument("path", help="run-directory-relative path")
    p_show.add_argument("overrides", nargs="*")
    p_show.set_defaults(func=cmd_show)

    p_grader = sub.add_parser("grader", help="re-run the exit gate")
    add_common(p_grader)
    p_grader.add_argument("--json", action="store_true")
    p_grader.add_argument("overrides", nargs="*")
    p_grader.set_defaults(func=cmd_grader)

    p_instances = sub.add_parser("instances", help="generate the problem's data")
    add_common(p_instances)
    p_instances.add_argument("--force", action="store_true", help="regenerate from scratch")
    p_instances.add_argument("--out", default=None, help="output directory (default: problem data dir)")
    p_instances.add_argument("overrides", nargs="*")
    p_instances.set_defaults(func=cmd_instances)

    p_validate = sub.add_parser("validate", help="check the task config and the problem it selects")
    add_common(p_validate)
    p_validate.add_argument("overrides", nargs="*")
    p_validate.set_defaults(func=cmd_validate)

    p_doctor = sub.add_parser(
        "doctor",
        help="check the runtime prerequisites (agent CLI, data, baseline, prompts) before a run",
    )
    add_common(p_doctor)
    p_doctor.add_argument(
        "--probe",
        action="store_true",
        help="also send one 4-token messages request to the resolved model endpoint",
    )
    p_doctor.add_argument("--probe-timeout", type=float, default=20.0, help="seconds to wait for the probe")
    p_doctor.add_argument("overrides", nargs="*")
    p_doctor.set_defaults(func=cmd_doctor)

    p_clean = sub.add_parser("clean", help="remove run directories")
    add_common(p_clean)
    p_clean.add_argument("--all", action="store_true", help="actually delete (default: dry run)")
    p_clean.add_argument("overrides", nargs="*")
    p_clean.set_defaults(func=cmd_clean)

    p_version = sub.add_parser("version", help="print the version")
    p_version.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
