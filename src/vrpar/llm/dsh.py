"""The DeepSeek Harness (``dsh``) agent runtime.

What this backend does, and why each choice is what it is:

* spawns ``dsh --profile headless`` in the stage worktree and **writes the instruction to
  the child's stdin**. The task is not an argv entry: on Windows ``dsh`` resolves to a
  ``.cmd`` shim, which runs through ``cmd.exe`` and inherits its **8191-character command
  line limit**. A rendered stage instruction is 11k-38k characters, so passing it as argv
  was rejected before the process started (``命令行太长。``). ``headless`` documents
  ``echo "run the tests" | dsh --profile headless``, so stdin is the supported path;
* passes only launcher flags. ``--model`` is **not** a flag this CLI knows
  (``error: unknown option '--model'``); the model belongs to the profile, so it is
  reported in the log header and set through the profile or a ``--patch`` overlay;
* **inherits** ``DSH_HOME`` by default. DSH reads its provider credentials from that
  directory (``<DSH_HOME>/llm-deepseek/``), so redirecting it at a fresh directory per run
  hid the user's stored key and every turn died with ``MISSING_CREDENTIAL``. A task that
  wants isolated session state can still set ``llm.dsh_home``, but it then has to supply
  ``DEEPSEEK_API_KEY`` through the environment -- the isolation is what hides the store;
* passes the permission and tool modes through the environment DSH reads;
* decodes the child's output with the console code page as a fallback. ``cmd.exe`` reports
  its own errors in the OEM code page (cp936 on a Chinese install), so reading those bytes
  as UTF-8 produced ``������̫����`` in a report and hid the real message;
* reads the final assistant text out of the log.

``dsh`` is installed on some machines and not others, so a missing binary produces a
message that says what to install rather than an empty result. Everything verifiable
without a live model -- the argv, the environment, the stdin delivery and the log parsing
-- is asserted in `tests/test_dsh_backend.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from vrpar.llm.base import LLMBackend, LLMResult, extract_json_payload
from vrpar.llm.claude_code import MAX_CAPTURED_CHARS, _clean_env

#: Environment variables DSH itself reads, per CORAL's agent-runtimes guide.
DSH_HOME_VAR = "DSH_HOME"
PERMISSION_VAR = "DSH_PERMISSION_MODE"
TOOLS_VAR = "DSH_TOOLS_MODE"

#: The API key and base URL DSK honours for model traffic.
API_KEY_VAR = "DEEPSEEK_API_KEY"
BASE_URL_VAR = "DEEPSEEK_BASE_URL"

#: Variables Node's `os.tmpdir()` reads, in its own order of preference. DSH's `spill-local`
#: plugin creates its private root with `mkdtemp(join(tmpdir(), "dsh-spill-"))`, so pointing
#: these at a directory inside the run is what keeps it inside the sandbox.
TEMP_VARS = ("TMPDIR", "TMP", "TEMP")

DEFAULT_PROFILE = "headless"

#: Refuse to launch when the argv alone would exceed this. ``cmd.exe`` truncates at 8191,
#: and on Windows a ``.cmd`` shim runs through it, so anything near that limit fails with a
#: localised system message instead of a useful one. The instruction no longer counts -- it
#: goes on stdin -- so only ``extra_args`` and ``patch`` overlays can approach this.
MAX_COMMAND_LINE = 8000

#: Code pages to try when the child's bytes are not valid UTF-8. cp936 covers a Chinese
#: Windows, cp1252 a western one; both are byte-compatible enough to be worth trying before
#: giving up on a replacement-character decode.
CONSOLE_CODECS = ("utf-8", "cp936", "cp1252")


def resolve_dsh_binary(binary: str) -> str:
    """Return an executable path for the DeepSeek Harness CLI."""
    candidate = Path(binary).expanduser()
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(binary)
    if found:
        return found
    raise FileNotFoundError(
        f"DeepSeek Harness CLI not found ({binary!r}). Install it with "
        "`npm install --global @deepseek-ai/dsh`, make sure `dsh` is on PATH, set "
        "`DEEPSEEK_API_KEY`, or point `llm.runtime_options.command` at the binary."
    )


def build_command(
    binary: str = "dsh",
    profile: str = DEFAULT_PROFILE,
    patch: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """The launcher argv for one turn (public so tests can assert on it).

    Deliberately contains **no prompt and no model**:

    * the task is delivered on stdin (see ``run``), because a rendered instruction does not
      fit in a Windows command line;
    * ``--model`` is not a launcher option -- ``dsh --profile headless --model x`` answers
      ``error: unknown option '--model'``. The model is a profile setting.
    """
    cmd = [binary, "--profile", profile]
    for item in patch or []:
        cmd.extend(["--patch", str(item)])
    cmd.extend(extra_args or [])
    return cmd


def describe_command(cmd: list[str]) -> str:
    """The command line as the OS sees it, for the length check and for logs."""
    return " ".join(cmd)


def decode_console_output(raw: bytes) -> str:
    """Decode a child process's bytes, which may be in the console code page.

    ``cmd.exe`` writes its own diagnostics in the OEM code page rather than UTF-8. Decoding
    ``b'\\xc3\\xfc\\xc1\\xee...'`` as UTF-8 yields replacement characters that hide the
    message entirely; as cp936 it is ``命令行太长。`` ("the command line is too long"), which
    is what the report needed to say.
    """
    for codec in CONSOLE_CODECS:
        try:
            return raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _ensure_profile_dir(home: Path, profile: str) -> None:
    """Make the directory DSH is about to make itself, before DSH starts.

    DSH creates ``<DSH_HOME>/profiles/<profile>`` at boot and dies with
    ``EPERM ... mkdir`` before the model is ever called if it cannot. Probing the same
    operation first turns that into something this backend can report and route around.
    """
    (home / "profiles" / profile).mkdir(parents=True, exist_ok=True)


def resolve_dsh_home(
    configured: str,
    run_dir: Path | None,
    profile: str,
) -> tuple[str, str]:
    """Decide ``DSH_HOME`` for this turn, and explain any fallback.

    Returns ``(home, note)``. An empty ``home`` means "leave ``DSH_HOME`` inherited", which
    is what makes DSH find the credential store it was set up with.

    The inherited home is **probed** by performing the mkdir DSH is about to perform. When
    that fails -- a restricted token, an ACL granting only read, a home on a read-only
    mount -- DSH would otherwise die at boot with ``EPERM mkdir .../profiles/<profile>`` and
    the run would fail before any model call. Falling back to a run-local home keeps the turn
    possible; the cost is that the stored credential is no longer visible, so the note names
    the environment variable that replaces it.
    """
    if configured:
        home = Path(configured).expanduser()
        if not home.is_absolute() and run_dir is not None:
            home = Path(run_dir) / home
        _ensure_profile_dir(home, profile)
        return str(home), ""

    inherited = Path(os.environ.get(DSH_HOME_VAR) or (Path.home() / ".dsh")).expanduser()
    try:
        _ensure_profile_dir(inherited, profile)
    except OSError as exc:
        fallback = (Path(run_dir) if run_dir is not None else Path.cwd()) / ".dsh"
        # Not routed through the probe: this is the last resort, and if even a directory
        # inside the run cannot be created there is nothing left to fall back to.
        (fallback / "profiles" / profile).mkdir(parents=True, exist_ok=True)
        reason = getattr(exc, "strerror", None) or exc.__class__.__name__
        note = (
            f"DSH cannot write its profile to the inherited home {inherited} ({reason}); "
            f"using {fallback} for this run instead. That fallback has no credential store, "
            f"so {API_KEY_VAR} must be set in the launching environment. The alternative is "
            f"to make {inherited} writable, and DSH will use the credential it already has."
        )
        return str(fallback), note
    return "", ""


def dsh_env(
    extra: dict[str, str] | None = None,
    *,
    run_dir: Path | None = None,
    inherit_routing: bool = True,
    permission_mode: str = "",
    tools_mode: str = "",
    dsh_home: str = "",
    redirect_temp: bool = True,
) -> dict[str, str]:
    """The environment for one `dsh` turn.

    ``dsh_home`` is empty by default, which leaves ``DSH_HOME`` **unset** so DSH uses the
    user's real home. That is deliberate: the provider credentials live under
    ``<DSH_HOME>/llm-deepseek/``, and pointing the variable at a fresh directory per run
    made DSH report ``MISSING_CREDENTIAL`` even on an authenticated machine. Set an explicit
    ``dsh_home`` only to isolate a run, and supply ``DEEPSEEK_API_KEY`` when you do --
    isolation is exactly what hides the stored credential.

    ``redirect_temp`` points the child's temporary directory at ``<DSH_HOME>/tmp``, or at
    ``<run>/tmp`` when the home is inherited. That is not cosmetic: DSH runs under its own
    file sandbox, which refuses writes outside the workspace, while its ``spill-local``
    plugin creates a private root with ``mkdtemp(join(os.tmpdir(), "dsh-spill-"))``. Left
    alone, that call fails with
    ``EPERM: operation not permitted, mkdtemp 'C:\\Users\\...\\Temp\\dsh-spill-XXXXXX'``
    and the plugin never activates. A run-local temp dir keeps every write inside the
    workspace, which is also where the sandbox expects it.
    """
    env = _clean_env(dict(extra or {}), inherit_routing=inherit_routing)
    home: Path | None = None
    if dsh_home:
        home = Path(dsh_home).expanduser()
        if not home.is_absolute() and run_dir is not None:
            home = Path(run_dir) / home
        home.mkdir(parents=True, exist_ok=True)
        env[DSH_HOME_VAR] = str(home)
    if redirect_temp:
        scratch = (home or Path(run_dir or ".")) / "tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        for name in TEMP_VARS:
            env[name] = str(scratch)
    if permission_mode:
        env[PERMISSION_VAR] = permission_mode
    if tools_mode:
        env[TOOLS_VAR] = tools_mode
    return env


class DshBackend(LLMBackend):
    """Launch the DeepSeek Harness in a stage worktree and collect its answer."""

    name = "dsh"

    def __init__(self, llm_config: dict | None = None):
        cfg = dict(llm_config or {})
        options = dict(cfg.get("runtime_options") or {})
        self.model = str(options.get("model") or cfg.get("model") or "")
        self.binary = str(options.get("command") or cfg.get("dsh_bin") or "dsh")
        self.profile = str(options.get("profile") or DEFAULT_PROFILE)
        self.patch = [str(item) for item in (options.get("patch") or cfg.get("patch") or [])]
        self.permission_mode = str(
            options.get("permission_mode") or cfg.get("permission_mode") or ""
        )
        self.tools_mode = str(options.get("tools_mode") or cfg.get("tools_mode") or "")
        self.dsh_home = str(cfg.get("dsh_home") or "")
        self.extra_args = [str(item) for item in (cfg.get("extra_args") or [])]
        self.env_extra = {str(k): str(v) for k, v in (cfg.get("env") or {}).items()}
        self.inherit_routing_env = bool(cfg.get("inherit_routing_env", True))
        self.redirect_temp = bool(cfg.get("redirect_temp", True))
        self.run_dir = Path(str(cfg["_run_dir"])) if cfg.get("_run_dir") else None

    # -- introspection ------------------------------------------------------- #

    def command_for(self, prompt: str = "") -> list[str]:
        """The argv this backend would use, resolved binary included.

        `prompt` is accepted and ignored: it is delivered on stdin, so it is not part of the
        command line. The parameter stays so a caller can ask "what would you run for this
        turn" without special-casing this backend.
        """
        del prompt
        try:
            binary = resolve_dsh_binary(self.binary)
        except FileNotFoundError:
            binary = self.binary
        return build_command(
            binary=binary,
            profile=self.profile,
            patch=self.patch,
            extra_args=self.extra_args,
        )

    def environment(self, dsh_home: str | None = None) -> dict[str, str]:
        """The environment for one turn (public so tests and diagnostics can inspect it).

        `dsh_home` overrides the configured value for this call; `run()` passes what the
        probe decided. Left as `None` it uses what the task configured, which keeps this
        method side-effect free -- the probe creates directories and belongs in `run()`.
        """
        chosen = self.dsh_home if dsh_home is None else dsh_home
        return dsh_env(
            self.env_extra,
            run_dir=self.run_dir,
            inherit_routing=self.inherit_routing_env,
            permission_mode=self.permission_mode,
            tools_mode=self.tools_mode,
            dsh_home=chosen,
            redirect_temp=self.redirect_temp,
        )

    # -- one turn ------------------------------------------------------------ #

    def _header(self, cwd: Path, env: dict[str, str], note: str = "") -> str:
        """The log header, written before the launch is attempted.

        Recorded first on purpose: when the binary is missing, the command line is refused
        or the profile cannot be created, the log is the only place that says which runtime
        and profile the pipeline was asking for. The model is here rather than in the argv
        because ``--model`` is not a flag this CLI accepts.
        """
        header = (
            "# vrpar agent turn (dsh)\n"
            f"# cwd: {Path(cwd).resolve()}\n"
            f"# profile: {self.profile}\n"
            f"# model: {self.model or '(profile default)'}\n"
            f"# {DSH_HOME_VAR}: {env.get(DSH_HOME_VAR, '(inherited)')}\n"
            "# task: piped on stdin\n"
        )
        if note:
            header += f"# note: {note}\n"
        return header + "\n"

    def run(self, prompt: str, cwd: Path, log_path: Path, timeout_s: int = 3600) -> LLMResult:
        started = time.time()
        cwd = Path(cwd)
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Decide DSH_HOME before anything can fail: if the inherited home is not writable,
        # DSH would die at boot and the run would fail before a model call. The note travels
        # into the log so the reason is visible in the run report, not just in this process.
        home, note = resolve_dsh_home(self.dsh_home, self.run_dir, self.profile)
        env = self.environment(home)
        header = self._header(cwd, env, note)

        try:
            binary = resolve_dsh_binary(self.binary)
        except FileNotFoundError as exc:
            _write_text(log_path, header + f"# error: {exc}\n")
            return LLMResult(
                returncode=127, error=str(exc), backend=self.name, log_path=str(log_path)
            )

        cmd = build_command(
            binary=binary,
            profile=self.profile,
            patch=self.patch,
            extra_args=self.extra_args,
        )

        # A too-long argv is refused here, with a message that names the cause, instead of
        # letting cmd.exe answer with a localised "the command line is too long".
        line = describe_command(cmd)
        if len(line) > MAX_COMMAND_LINE:
            error = (
                f"refusing to launch: the command line is {len(line)} characters and the "
                f"limit is {MAX_COMMAND_LINE} (cmd.exe truncates at 8191, and a `.cmd` shim "
                f"runs through it). Shorten `llm.extra_args` or the `patch` overlay list; "
                f"the instruction itself is not on the command line."
            )
            _write_text(log_path, header + f"# error: {error}\n")
            return LLMResult(
                returncode=126, error=error, backend=self.name, log_path=str(log_path)
            )

        # `newline=""` so the header is written with the exact bytes we later strip: Windows
        # text mode would turn '\n' into '\r\n' and the byte-prefix match in `read_answer`
        # would silently fail, leaving the header glued to the answer.
        with log_path.open("w", encoding="utf-8", errors="replace", newline="") as log_file:
            log_file.write(header)
            log_file.flush()
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    env=env,
                    # The task goes here. `headless` reads it when no positional task is
                    # given: `echo "run the tests" | dsh --profile headless`.
                    stdin=subprocess.PIPE,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as exc:
                return LLMResult(
                    returncode=126,
                    error=f"failed to launch dsh: {exc}",
                    backend=self.name,
                    log_path=str(log_path),
                )
            try:
                proc.communicate(input=prompt, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return LLMResult(
                    text=self.read_answer(log_path, header)[:MAX_CAPTURED_CHARS],
                    returncode=124,
                    duration_s=time.time() - started,
                    backend=self.name,
                    log_path=str(log_path),
                    error=f"agent turn exceeded {timeout_s}s and was killed",
                )

        text = self.read_answer(log_path, header)
        return LLMResult(
            text=text[:MAX_CAPTURED_CHARS],
            payload=extract_json_payload(text),
            returncode=proc.returncode,
            duration_s=time.time() - started,
            backend=self.name,
            log_path=str(log_path),
            error="" if proc.returncode == 0 else f"dsh exited with code {proc.returncode}",
        )

    @staticmethod
    def read_answer(log_path: Path, header: str = "") -> str:
        """The child's own output, decoded with the console code page as a fallback.

        The header is stripped by bytes so that decoding the payload cannot be corrupted by
        the UTF-8 header in front of it (or the other way round).
        """
        try:
            raw = Path(log_path).read_bytes()
        except OSError:
            return ""
        prefix = header.encode("utf-8")
        if header and raw.startswith(prefix):
            raw = raw[len(prefix) :]
        return decode_console_output(raw).strip()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def describe_environment() -> dict[str, Any]:
    """What a `vrpar doctor` style command wants to know about this runtime."""
    found = shutil.which("dsh")
    return {
        "binary": found or "dsh (not on PATH)",
        "DEEPSEEK_API_KEY": "set" if os.environ.get(API_KEY_VAR) else "unset",
        BASE_URL_VAR: os.environ.get(BASE_URL_VAR, "unset"),
    }
