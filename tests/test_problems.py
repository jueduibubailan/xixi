"""Problem-plugin tests: loading, hooks, path resolution, and scaffolding.

These are the tests that keep the engine variant-agnostic. If someone
re-introduces a hardcoded variant name in the core, the "new problem" test below
will not exercise it and the scaffolding test will fail to produce a loadable
problem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cvrp.config import ConfigError, load_config
from cvrp.problems import (
    DEFAULT_BEST_REGEX,
    ProblemError,
    find_problem_dir,
    list_problems,
    load_problem,
)
from cvrp.problems.scaffold import scaffold_problem
from tests.conftest import PACKAGE_PROBLEMS, baseline_of, write_project

TASK_YAML = """
task:
  name: "problem test"
  slug: "probe"
  description: "for plugin tests"

problem:
  slug: "cvrptw"

llm:
  backend: "mock"
  model: "mock"

stages:
  - name: analysis
    kind: agent
    produces: ["artifacts/analysis/analysis.json"]
"""


def test_shipped_cvrptw_problem_loads():
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    assert plugin.slug == "cvrptw"
    assert plugin.kind == "vrp"
    assert plugin.program == "run.py"
    assert plugin.program_relpath == "artifacts/generated/run.py"
    assert plugin.data_dir_name == "instances/data/cvrptw"
    assert plugin.direction == "maximize"
    assert plugin.score_kind == "inverse_cost"
    assert plugin.instance_glob == "n20/*.json"


def test_every_stage_prompt_resolves_to_a_readable_file():
    """Prompts come from the engine's shared pack unless the problem overrides one.

    The shared pack renders variant-neutral text (`{problem}`, `{n}`, `{variant_label}`,
    `{task_description}`), which is what lets a problem directory hold only its task file,
    its seed and its grader. The property worth pinning is that *every* stage a task can
    name resolves to a non-empty file: a missing prompt renders an empty instruction, and
    that failure mode costs a whole run.
    """
    from cvrp.pipeline.stages import STAGE_PROMPT_KEYS

    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    for stage, key in STAGE_PROMPT_KEYS.items():
        path = Path(plugin.prompt(key))
        assert path.is_file(), (stage, key, path)
        assert path.read_text(encoding="utf-8").strip(), f"{stage} prompt is empty"

    # prompts a stage asks for by an explicit key rather than by stage name
    for key in (
        "analysis_judge",
        "codegen_judge",
        "error_narrate",
        "code_review",
        "error_analyse",
    ):
        assert Path(plugin.prompt(key)).is_file(), key


def test_a_problem_can_override_one_prompt_by_convention(scratch: Path):
    """`prompts/<basename>.md` next to a problem's spec replaces just that one prompt."""
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    default = Path(plugin.prompt("analysis"))
    assert default.parent.name == "prompts"

    override_dir = scratch / "problem"
    (override_dir / "prompts").mkdir(parents=True)
    (override_dir / "prompts" / "s1_analysis.md").write_text("# custom\n", encoding="utf-8")
    plugin.root = override_dir
    assert Path(plugin.prompt("analysis")).read_text(encoding="utf-8") == "# custom\n"
    # and the prompts it did not override still come from the pack
    assert Path(plugin.prompt("codegen")) == default.parent / "s2_codegen.md"


def test_agent_context_comes_from_the_problem():
    """The context file is read from the problem directory, not the engine.

    It is rendered with the task scale on the way out, so the comparison is
    against the file with its scale placeholders substituted.
    """
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    context = plugin.agent_context()
    raw = (plugin.root / "context.md").read_text(encoding="utf-8").strip()
    assert context == plugin.render(raw).strip()
    assert "checkpoint" in context.lower()
    assert "data/instances" in context
    # the default scale is substituted, not left as braces
    assert "{n}" not in context
    assert "problem_size (customers) = 20" in context


def test_generated_layout_is_declared_by_the_problem():
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    paths = plugin.generated_paths
    assert "artifacts/generated/run.py" in paths
    # the deliverable is named after the problem, one file per baseline role
    tag = plugin.problem_tag
    assert tag == "CVRPTW"
    assert f"artifacts/generated/{tag}Env.py" in paths
    assert f"artifacts/generated/{tag}Model.py" in paths
    assert f"artifacts/generated/{tag}ProblemDef.py" in paths
    # required_paths are *names* relative to the generated dir; generated_paths
    # are run-relative, because that is where the run directory keeps them
    assert plugin.required_paths == ["run.py"]


def test_the_deliverable_is_one_to_one_with_pomo():
    """One entry per baseline role, plus the CLI and its README -- nothing more.

    The naming rule is `{problem}<Role>.py`; a baseline-named file (`CVRPEnv.py`)
    would mean the agent edited a copy instead of exporting under the problem tag,
    and an undeclared file would be extra generated code.
    """
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    generated = plugin.spec["generated"]
    tag = plugin.problem_tag

    assert "utils.py" not in generated["files"]
    assert "pomo/POMO/utils/utils.py" not in generated["files"]

    for name in generated["files"]:
        if name in {"run.py", "README.md"} or name.startswith(("train_n", "test_n")):
            continue
        assert name.startswith(tag), f"{name} is not named after {tag}"

    # every declared deliverable is adapted from a real baseline file
    pomo = baseline_of(plugin)
    for name, source in plugin.correspondence.items():
        if source is None:
            continue
        assert (pomo / source).is_file(), f"{source} is not in the baseline"
        if name not in {"run.py", "README.md"}:
            assert name.startswith(tag) or name.startswith(("train_n", "test_n"))


def test_cli_args_are_built_from_the_spec():
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    instance = Path("instances") / "i.json"
    solution = Path("out") / "s.json"
    args = plugin.cli_args(instance, 7, solution)
    assert args[0] == "--path"
    assert args[1] == str(instance)
    assert args[2] == "--iteration"
    assert args[3] == "7"
    assert args[4] == "--save-solution"
    assert args[5] == str(solution)


def test_cli_args_allow_a_problem_without_an_instance_flag(scratch: Path):
    plugin = _write_minimal_problem(scratch / "nopath", cli={"path_arg": None, "iteration_arg": None})
    assert plugin.cli_args(Path("i.json"), 5, None) == []


def test_checks_hooks_are_picked_up():
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    assert callable(getattr(plugin.checks_module, "verify", None))
    assert callable(getattr(plugin.checks_module, "build_dataset", None))
    assert plugin.revision_hints(), "cvrptw declares error-class hints"


def test_scoring_kinds(scratch: Path):
    inverse = _write_minimal_problem(scratch / "inv", scorer={"kind": "inverse_cost", "scale": 2.0})
    assert inverse.score(4.0) == 0.5
    assert inverse.score(0.0) == 0.0
    assert inverse.score(None) == 0.0

    identity = _write_minimal_problem(scratch / "id", scorer={"kind": "identity", "scale": 3.0})
    assert identity.score(4.0) == 12.0

    binary = _write_minimal_problem(scratch / "bin", scorer={"kind": "binary", "scale": 1.0})
    assert binary.score(4.0) == 1.0


def test_default_verify_is_honest_about_being_generic(scratch: Path):
    """A problem without checks.py must say so, not pretend to have verified."""
    plugin = _write_minimal_problem(scratch / "nochecks", checks=False)
    assert plugin.checks_module is None
    solution = scratch / "sol.json"
    solution.write_text(json.dumps({"routes": [[0, 1, 0]], "cost": 1.0}), encoding="utf-8")
    result = plugin.verify(scratch / "inst.json", solution)
    assert result["feasible"] is True
    assert result["checks"]["json_parseable"] is True
    assert "verified only that the dump is valid JSON" in result["note"]
    assert result["solution_keys"] == ["cost", "routes"]

    broken = scratch / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert plugin.verify(scratch / "inst.json", broken)["feasible"] is False


def test_missing_solution_file_is_a_violation(scratch: Path):
    plugin = _write_minimal_problem(scratch / "missing", checks=False)
    result = plugin.verify(scratch / "inst.json", scratch / "nope.json")
    assert result["feasible"] is False
    assert result["violations"]
    assert "missing" in result["violations"][0].lower()


def test_empty_hook_result_is_not_a_pass(scratch: Path):
    """A verify() hook that returns nothing must not be read as 'feasible'."""
    plugin = _write_minimal_problem(scratch / "empty", checks=True, checks_verify_body="    return {}\n")
    solution = scratch / "sol.json"
    solution.write_text("{}", encoding="utf-8")
    result = plugin.verify(scratch / "inst.json", solution)
    assert result["feasible"] is False
    assert result["violations"]


def test_find_problem_dir_prefers_the_project_copy(scratch: Path):
    project = write_project(scratch / "project", TASK_YAML, problem="cvrptw")
    config = load_config(project / "task.yaml", cwd=project)
    root = find_problem_dir("cvrptw", config)
    assert root == project / "problems" / "cvrptw"


def test_find_problem_dir_falls_back_to_the_package(scratch: Path):
    project = write_project(scratch / "project", TASK_YAML, problem="cvrptw")
    (project / "problems").rename(project / "problems-away")
    config = load_config(project / "task.yaml", cwd=project)
    root = find_problem_dir("cvrptw", config)
    assert root == PACKAGE_PROBLEMS / "cvrptw"


def test_find_problem_dir_ignores_a_directory_without_a_spec(scratch: Path):
    """A stray directory must not shadow a real problem.

    This happens in practice: a problem's baseline payload can live next to the
    project's `problems/` directory, and picking that directory instead of the
    package copy would fail later with a confusing "problem.yaml not found".
    """
    project = write_project(scratch / "project", TASK_YAML, problem="cvrptw")
    (project / "problems" / "cvrptw").rename(project / "problems-away")
    # a directory with the right name but no spec
    stray = project / "problems" / "cvrptw" / "seed" / "pomo"
    stray.mkdir(parents=True)
    (stray / "CVRProblemDef.py").write_text("# payload only\n", encoding="utf-8")

    config = load_config(project / "task.yaml", cwd=project)
    assert find_problem_dir("cvrptw", config) == PACKAGE_PROBLEMS / "cvrptw"


def test_missing_problem_slug_is_a_config_error(scratch: Path):
    project = scratch / "project"
    (project / "configs").mkdir(parents=True)
    (project / "task.yaml").write_text(TASK_YAML.replace('slug: "cvrptw"', 'slug: ""'), encoding="utf-8")
    with pytest.raises(ConfigError, match="problem.slug"):
        load_config(project / "task.yaml", cwd=project)


def test_unknown_problem_slug_is_a_config_error(scratch: Path):
    project = scratch / "project"
    (project / "configs").mkdir(parents=True)
    (project / "task.yaml").write_text(TASK_YAML.replace('"cvrptw"', '"no-such-variant"'), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(project / "task.yaml", cwd=project)
    with pytest.raises(ProblemError):
        find_problem_dir("no-such-variant", {"_project_root": str(project)})


def test_list_problems_finds_the_bundles():
    """`list_problems` scans `problems/`, where a bundle is `task.yaml` + `seed/`."""
    slugs = list_problems({"_project_root": str(Path.cwd())})
    assert "cvrptw" in slugs
    assert "cvrpltw" in slugs, "a second bundle must be discoverable too"


def test_contract_reads_cli_and_regex_from_the_problem(scratch: Path):
    from cvrp.executor.contract import Contract

    project = write_project(scratch / "project", TASK_YAML, problem="cvrptw")
    config = load_config(project / "task.yaml", cwd=project)
    contract = Contract.from_config(config)
    assert contract.program == "artifacts/generated/run.py"
    assert contract.path_flag == "--path"
    assert contract.save_solution_flag == "--save-solution"
    assert contract.best_regex == DEFAULT_BEST_REGEX
    instance, solution = Path("i.json"), Path("s.json")
    args = contract.cli_args(instance, 3, solution)
    assert args == ["--path", str(instance), "--iteration", "3", "--save-solution", str(solution)]


# --------------------------------------------------------------------------- #
# scaffolding: the "add a new variant" path
# --------------------------------------------------------------------------- #


def test_scaffold_produces_a_loadable_problem(scratch: Path):
    target = scratch / "problems" / "my-variant"
    written = scaffold_problem(
        slug="my-variant",
        name="My Variant",
        target=target,
        description="A variant with drones and time windows.",
        variant_label="MyVariant-on-POMO",
        baseline="./baseline",
    )
    assert written
    assert (target / "problem.yaml").is_file()
    assert (target / "checks.py").is_file()
    assert (target / "prompts" / "s1_analysis.md").is_file()

    plugin = load_problem(target, slug="my-variant")
    assert plugin.name == "My Variant"
    assert plugin.spec["variant_label"] == "MyVariant-on-POMO"
    assert plugin.program == "run.py"
    assert Path(plugin.prompt("analysis")).is_file()
    # the description is embedded in the prompts, so the agents see the variant
    assert "drones" in Path(plugin.prompt("analysis")).read_text(encoding="utf-8")
    assert "drones" in Path(plugin.prompt("codegen")).read_text(encoding="utf-8")


def test_the_staged_baseline_is_importable_as_staged():
    """The staged layout must actually work, helpers included.

    Upstream POMO imports its helpers as ``from CVRP.POMO.utils.utils import ...``
    because the upstream repository is named ``CVRP``. The staging directory is
    called ``pomo/``, so that prefix cannot resolve. A baseline whose imports cannot
    be followed teaches the agent to copy a broken import, which then fails at the
    executor with ``ModuleNotFoundError`` - and the failure looks like the agent's
    fault rather than the baseline's.
    """
    import importlib
    import sys

    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    pomo = baseline_of(plugin) / "pomo"
    assert (pomo / "POMO" / "utils" / "utils.py").is_file(), "the helper must ship with the baseline"

    offenders = [
        str(path.relative_to(pomo))
        for path in sorted(pomo.rglob("*.py"))
        if "from CVRP." in path.read_text(encoding="utf-8")
        or "import CVRP." in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"baseline files still use the upstream package name: {offenders}"

    # `pomo.POMO.utils.utils` needs the *parent* of `pomo/` on sys.path: `pomo` is
    # an implicit namespace package, and the parent is what the generation prompt
    # tells the agent to bootstrap.
    sys.path.insert(0, str(pomo.parent))
    try:
        module = importlib.import_module("pomo.POMO.utils.utils")
        assert hasattr(module, "create_logger")
        assert hasattr(module, "copy_all_src")
    finally:
        sys.path.remove(str(pomo.parent))
        for name in list(sys.modules):
            if name == "pomo" or name.startswith("pomo."):
                del sys.modules[name]


def test_the_spec_and_prompts_encode_the_deliverable_contract():
    """The strategy: adapt POMO in place, export it under the problem's name."""
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    files = (plugin.spec.get("generated") or {}).get("files") or []
    assert "utils.py" not in files
    assert "pomo/POMO/utils/utils.py" not in files

    codegen = Path(plugin.prompt("codegen")).read_text(encoding="utf-8")
    for phrase in (
        "Edit the baseline in place",
        "under its new name",
        "baseline-correspondence",
        "Match the existing style",
        "Nothing else belongs",
        "must stay commented out",
        "Do not tune them down",
    ):
        assert phrase in codegen, f"the generation prompt lost its '{phrase}' rule"
    # and the old "same relative path" framing must be gone
    assert "package of cooperating modules" not in codegen
    assert "same relative path" not in codegen
    assert "Carry the baseline's helper modules over" not in codegen

    context = plugin.agent_context()
    assert "What the deliverable may contain" in context
    assert "CVRPTWEnv.py" in context
    # the deliverable tree is shown with the concrete directory, not a placeholder
    # (`{generated_dir}` is a stage variable and would not be substituted here)
    assert "{generated_dir}" not in context


def test_judge_and_revise_prompts_share_the_deliverable_rules():
    plugin = load_problem(PACKAGE_PROBLEMS / "cvrptw", slug="cvrptw")
    judge = Path(plugin.prompt("codegen_judge")).read_text(encoding="utf-8")
    assert "File set and naming" in judge
    assert "named after the *baseline*" in judge
    assert "Correspondence lines" in judge
    assert "Parameter values" in judge
    assert "Style fidelity" in judge

    revise = Path(plugin.prompt("revise")).read_text(encoding="utf-8")
    assert "may **not** add a new module" in revise
    assert "already installed" in revise
    assert "correspondence" in revise

    analyse = Path(plugin.prompt("error_analyse")).read_text(encoding="utf-8")
    assert "path problem, not a missing package" in analyse
    assert "FileNotFoundError" in analyse

    review = Path(plugin.prompt("code_review")).read_text(encoding="utf-8")
    assert "Read every file in the deliverable" in review
    assert "missing_symbol" in review
    assert "Do not edit any code in this step" in review


def test_scaffold_prompts_render_through_the_engine(scratch: Path):
    """A scaffolded problem's prompts must render with real values, not braces.

    The scaffold's own guidance comments mention the placeholder vocabulary on
    purpose, and the renderer blanks unknown placeholders, so the assertion is
    about the *content* placeholders: the variant description and the stage's
    declared outputs must reach the agent.
    """
    from cvrp.pipeline.stages import render_instruction

    target = scratch / "problems" / "scaffolded"
    scaffold_problem(
        slug="scaffolded",
        name="Scaffolded Variant",
        target=target,
        description="A variant with drones and soft time windows.",
        variant_label="Scaffolded-on-POMO",
    )
    plugin = load_problem(target, slug="scaffolded")
    config = {
        "_problem": plugin,
        "_project_root": str(scratch),
        "_task_dir": str(scratch),
        "problem": {"slug": "scaffolded"},
        "task": {"name": "t", "description": "task level description"},
        "llm": {"model": "m"},
    }
    text = render_instruction(
        {"name": "analysis", "kind": "agent", "produces": ["artifacts/analysis/analysis.json"]},
        config,
        worktree=scratch / "wt",
        run_dir=scratch / "run",
    )
    assert "Scaffolded Variant" in text
    assert "drones and soft time windows" in text
    assert "artifacts/analysis/analysis.json" in text
    assert "{task_description}" not in text


def test_scaffold_refuses_and_then_overwrites(scratch: Path):
    target = scratch / "problems" / "dup"
    scaffold_problem(slug="dup", name="Dup", target=target)
    scaffold_problem(slug="dup", name="Dup", target=target, force=True)
    assert (target / "problem.yaml").is_file()


def test_scaffolded_problem_is_listed_and_configurable(scratch: Path):
    project = scratch / "project"
    (project / "configs").mkdir(parents=True)
    scaffold_problem(slug="brand-new", name="Brand New", target=project / "problems" / "brand-new")
    task = TASK_YAML.replace("cvrptw", "brand-new")
    (project / "task.yaml").write_text(task, encoding="utf-8")
    config = load_config(project / "task.yaml", cwd=project)
    assert config["_problem"].slug == "brand-new"
    assert "brand-new" in list_problems(config)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

MINIMAL_SPEC = """
slug: {slug}
name: "{name}"
checks: {checks}
prompts:
  analysis: prompts/s1_analysis.md
generated:
  dir: artifacts/generated
  program: run.py
  files: [run.py]
cli: {cli}
scorer: {scorer}
data:
  dir: instances/data/{slug}
"""


def _write_minimal_problem(
    root: Path,
    cli: dict | None = None,
    scorer: dict | None = None,
    checks: bool = True,
    checks_verify_body: str = "    return {'feasible': True, 'violations': [], 'checks': {}}\n",
) -> object:
    """Build a minimal problem directory and load it.

    ``checks=False`` simulates a problem with no ``checks.py`` at all, which is
    the state every new variant starts in.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "s1_analysis.md").write_text("# analysis\n", encoding="utf-8")
    (root / "problem.yaml").write_text(
        MINIMAL_SPEC.format(
            slug=root.name,
            name=root.name,
            checks="checks.py" if checks else "missing.py",
            cli=json.dumps(cli or {"path_arg": "--path", "iteration_arg": "--iteration"}),
            scorer=json.dumps(scorer or {"kind": "inverse_cost", "scale": 1.0}),
        ),
        encoding="utf-8",
    )
    if checks:
        (root / "checks.py").write_text(
            "def verify(instance_path, solution_path, spec):\n" + checks_verify_body,
            encoding="utf-8",
        )
    return load_problem(root, slug=root.name)
