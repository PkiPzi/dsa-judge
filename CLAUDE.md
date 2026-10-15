# dsa-judge

Local-first DSA practice judge: add questions from photos/text/voice, auto-build hidden tests from a reference solution, solve LeetCode-style in nvim/VS Code with a `dsa` CLI.

The full design is in @docs/PLAN.md. Read it before starting any phase. Work one phase at a time; each phase's acceptance criteria are in PLAN.md §11.

## Hard rules
- **No real problems in this repo, ever.** Fixtures are well-known public problems with statements written in our own words. Never paste problem text from the user's bank into code, tests, commits, PRs or logs.
- **All AI calls go through `dsajudge.ai` providers, loopback hosts only.** No other network calls anywhere in the code: no telemetry, no update checks, no remote APIs.
- **The AI never produces expected outputs or verdicts.** Expected outputs come from executing refs or brutes. Every AI-produced artifact is verified by execution.
- **Do not install Ollama, download models, or call real models in the cloud environment.** There is no GPU here. Use `FakeLLM` / `FakeTranscriber`. Real-model tests are marked `@pytest.mark.local` and skipped by default.
- Stubs and drivers are rendered from templates in `src/dsajudge/lang/templates/`; they are never LLM-generated.

## Environments
- **Cloud sessions (where you are building):** Ubuntu 24.04, g++, OpenJDK 21, Python 3 with uv preinstalled. `systemd-run --user` may be unavailable, so resource limits need a fallback path (see PLAN.md §5.2).
- **User's machine (where it runs):** EndeavourOS/Arch, Wayland, zsh, NVIDIA GTX 1650 with 4 GB VRAM. Don't hard-code Ubuntu paths. Use `shutil.which` and XDG dirs.

## Commands
```
uv sync --all-extras
uv run dsa --help
uv run pytest                 # default suite, no models, no network
uv run pytest -m local        # needs Ollama + whisper.cpp (user's machine only)
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

## Conventions
- Python ≥ 3.11 with type hints everywhere. pydantic v2 models for anything read from or written to disk.
- Prompts live in `src/dsajudge/ai/prompts/*.md`, not inline strings.
- Tests use a temp `DSA_HOME`; never touch `~/dsa-bank`.
- Keep the core judge free of AI dependencies; AI packages go in the `[ai]` extra.
- One branch and PR per phase. The PR description says what was built, what was deferred, and the exact commands to try it on the user's machine.
- If a design decision in PLAN.md changes, update the plan and add a dated line to its Decisions log in the same PR.
- After Phase 1 ships, `problem.yaml` schema changes need a migration, not a breaking edit.
