# Prompts for Claude Code

Paste one phase at a time into a cloud session at claude.ai/code with this repo selected. For the **first message of each phase**, set the mode dropdown to **Plan**, so Claude proposes the design and waits for your approval before editing. Once you approve, switch to **Accept edits** (or **Auto** if your account offers it).

Merge each phase's PR before starting the next one; every phase starts from `main`.

---

## Phase 0 + 1: skeleton and function-mode judge

```
Read CLAUDE.md and docs/PLAN.md fully, then implement Phase 0 and Phase 1 from PLAN.md §11.

Before writing code, show me:
1. the module breakdown and the main classes/functions,
2. the problem.yaml pydantic schema (check it against fixtures/problems/max-subarray/problem.yaml),
3. the canonical JSON I/O and how the C++, Java and Python drivers parse it and call Solution,
4. how batch execution, per-test timing, TLE re-runs, memory limits (with the no-systemd fallback) and stack size work,
5. how part unlocking and the work directory look on disk.

After I approve:
- Implement it with tests. Golden-file tests for stub/driver codegen; integration tests that actually compile and run C++, Java and Python.
- Add fixtures/solutions/max-subarray/ with correct, wrong, slow (O(n^2)), crash, deep-recursion and memory-hog variants in all three languages, plus a small script in tests/ that produces large manual test cases.
- Add a SessionStart hook in .claude/settings.json that runs `uv sync --all-extras` only when CLAUDE_CODE_REMOTE is "true".
- Add a GitHub Actions workflow running ruff, mypy and pytest.

Done means: every Phase 0 and Phase 1 acceptance criterion passes here and CI is green. Then open a PR whose description lists what was built, what was deferred, and the exact commands for me to try it on Arch Linux with nvim and VS Code.
```

## Phase 2: test generation and oracles

```
Read CLAUDE.md and docs/PLAN.md. Phase 1 is merged. Implement Phase 2 from PLAN.md §11.

Before writing code, show me:
1. the constraint DSL grammar and how dependent params (k <= len(nums)) are evaluated,
2. the generator library API and the list of flavors per type (arrays, strings, grids, trees, graphs),
3. the test plan, seeding and manifest format, and how large tests are regenerated from the cache,
4. the stress/oracle flow, including brute-only mode, promote and dispute,
5. how stdio mode renders inputs and compares outputs.

After I approve, implement with tests. Acceptance:
- `dsa build max-subarray` gives parts 1–2 in ref mode and part 3 in brute-only mode;
- a deliberately wrong ref is caught by stress with the smallest counterexample;
- a sample corrupted from 10^5 to 105 is caught by the validator;
- the weak-test warning fires when TL is set very high;
- generated inputs for every type always pass the validator (property tests).

Open a PR with the same description format as Phase 1.
```

## Phase 3: local AI intake

```
Read CLAUDE.md and docs/PLAN.md. Phases 1–2 are merged. Implement Phase 3 from PLAN.md §11.

There is no GPU or Ollama in this environment. Build everything against the LLM/Transcriber protocols, test with FakeLLM/FakeTranscriber, and mark real-model tests @pytest.mark.local. Do not download models.

Before writing code, show me:
1. the provider classes, including how OllamaLLM rejects non-loopback hosts and unloads models (keep_alive 0),
2. the intake stages and inbox layout, and how --resume works,
3. the JSON schemas for the segmentation pass and the per-part extraction pass, and the prompt files,
4. the review loop in $EDITOR and what the checklist header shows,
5. the verify-and-retry loops for AI-written brute, gen.py and checker.py.

After I approve, implement with tests. Synthetic intake fixtures: render statement images from text with Pillow at test time (include a superscript 10^5 and a Python code block whose indentation matters); use recorded transcripts for audio.

Acceptance: with fakes, `dsa add` on photo + voice + code inputs produces the expected reviewed problem.yaml and a built problem; `dsa followup` and `dsa ref` work; `dsa doctor --models` exists. Also give me a short LOCAL_TESTING.md: how to install Ollama + whisper.cpp on EndeavourOS with a GTX 1650, which models to pull, and how to run `pytest -m local`.

Open a PR with the same description format.
```

## Phase 4: polish (after interviews)

```
Read CLAUDE.md and docs/PLAN.md. Implement Phase 4: stats and the spaced-repetition review queue, design problems (mode: ops), export of stdio parts to HackerRank and HackerEarth test-case zip formats (research and cite their current upload formats first), shell completions, and PyPI packaging with a publish workflow. Plan first, then implement with tests, then open a PR.
```

---

## Between phases: test on your machine
```
git pull
uv sync --all-extras
uv run dsa doctor
uv run pytest
```
Then try the fixture yourself: `DSA_HOME=/tmp/dsa-try uv run dsa open max-subarray`. If something breaks, paste the command and output into the same cloud session, or open a new one with "Fix: <what happened>".
