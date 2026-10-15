# dsa-judge — design plan

A local-first DSA practice judge. Interview questions are added from **photos, typed text or voice notes**. The tool builds a hidden test suite from a reference solution and the constraints, and the user solves them **LeetCode-style** (fill in a `class Solution` method) in their own editor (nvim / VS Code), with a CLI that runs and submits like an online judge. Follow-up questions are separate **parts** that unlock one after another, like a real interview.

The CLI command is `dsa`. The Python package is `dsajudge` (rename freely before publishing to PyPI).

---

## 1. Non-negotiable rules

1. **Problem content never leaves the machine.** All AI runs locally (Ollama on loopback, whisper.cpp). The tool refuses any non-loopback AI endpoint. No telemetry, no update checks, no other network calls.
2. **The problem bank lives outside this repo** in `$DSA_HOME` (default `~/dsa-bank`). This repo holds code and *public* fixtures only. The user's questions will later be published on HackerRank/HackerEarth, so leaking them early defeats the purpose.
3. **The AI never decides correctness.** Expected outputs come from running a reference solution, or from a brute-force oracle that is labelled *unverified*. Every AI-produced artifact (brute, generator, checker, transcription) is checked by executing code, and retried with the error fed back.
4. **A human review gate follows intake.** Nothing is built from an unreviewed transcription.

## 2. Target machines

- **Daily driver:** EndeavourOS (Arch), KDE Plasma on Wayland, zsh, Alacritty. NVIDIA GTX 1650 with **4 GB VRAM**, so only small local models (≈2–4B at Q4) fit fully on GPU, and only one model can be loaded at a time.
- **Build environment:** Claude Code cloud sessions (Ubuntu 24.04, no GPU, no Ollama). Everything except real-model calls must run and be tested there.
- Practice language: **C++** by default. Reference solutions may be **C++, Java or Python** (some come from friends).

## 3. User workflows

### 3.1 Add a problem
```
dsa add [--slug NAME] [FILES...] [--clip] [--text] [--resume SLUG]
```
- `FILES`: any mix of images (`.jpg .jpeg .png .webp .heic`), audio (`.m4a .mp3 .ogg .opus .wav`), text (`.txt .md`) and code (`.cpp .cc .java .py`).
- `--clip` reads clipboard text (`wl-paste`, falling back to `xclip -o`). `--text` opens `$EDITOR` for pasting.
- Pipeline: **collect → transcribe → structure → review → build** (see §7). Every stage writes into `$DSA_HOME/inbox/<slug>/`, so a crash or Ctrl-C can resume with `--resume`.

### 3.2 Add a follow-up later
```
dsa followup SLUG [FILES...] [--clip] [--text]
```
Appends part N+1 through the same pipeline. The structuring step gets part N's signature and constraints as context, since a follow-up usually changes only a little.

### 3.3 Attach a reference solution later
```
dsa ref SLUG --part N FILE      # code file or photo of code; language auto-detected, overridable with --lang
```
The new ref must compile, pass samples, and agree with the existing oracle on all tests (or it is rejected with the first disagreement shown).

### 3.4 Practice
```
dsa list [--tag dp] [--unsolved] [--part-status]
dsa open SLUG | --next [--lang cpp|java|python]   # opens the current unlocked part
dsa run [--case '<json args, one per line>']      # samples + optional custom case
dsa submit [--strict]                             # full hidden suite
dsa debug [TEST_ID]                               # sanitizer rerun of a failing test
dsa watch                                         # rerun samples on every save
dsa show [SLUG]                                   # parts, oracle mode, status, best times
dsa reveal-ref SLUG --part N                      # spoiler; asks for confirmation
dsa promote [--part N]                            # brute-only parts: your AC solution becomes the ref
dsa dispute TEST_ID                               # brute-only parts: mark the oracle as suspect for this test
```
Inside a work directory, all practice commands infer slug and part from `.dsa.json`, so `dsa run` and `dsa submit` need no arguments.

### 3.5 Maintenance
```
dsa doctor [--models]     # toolchains, ollama/whisper reachability, model benchmark
dsa build SLUG [--part N] # (re)generate tests, refs, oracles
dsa rebuild SLUG          # wipe generated tests and rebuild from seeds
```

## 4. Problem model

### 4.1 Bank layout (`$DSA_HOME`, never in git)
```
$DSA_HOME/
  config.toml
  db.sqlite                      # attempts, timers, review queue
  inbox/<slug>/                  # intake scratch: raw/, transcript.md, draft.yaml
  problems/<slug>/
    problem.yaml
    raw/                         # original photos/audio/text, kept for re-processing
    transcript.md                # reviewed transcription
    part1/
      statement.md
      ref/                       # 0..n refs: ref.cpp, Solution.java, solution.py
      brute.py | brute.cpp       # optional oracle
      gen.py                     # optional custom generator
      checker.py                 # optional custom checker
      tests/
        manifest.json            # every test: id, group, seed, size, sha256(expected)
        small/<id>.in.json  <id>.out.json
  work/<slug>/part1/
    sol.cpp                      # the user's code, LeetCode-style stub
    statement.md
    prev.cpp                     # previous part's solution, if any
    .dsa.json                    # pointer back to the bank part
    .vscode/tasks.json
$XDG_CACHE_HOME/dsajudge/        # compiled binaries, regenerated large tests
```
Large tests are not stored: they are regenerated from `(slug, part, group, index)` seeds into the cache and verified against the manifest's hash.

### 4.2 `problem.yaml`
See `fixtures/problems/max-subarray/problem.yaml` for a complete example. Fields per part:

| Field | Meaning |
|---|---|
| `id`, `title`, `statement` | part number, title, path to statement markdown |
| `mode` | `function` (LeetCode-style, default) or `stdio` (full program, HackerRank style) |
| `signature` | `method`, `params[{name,type}]`, `returns`; optional `output_param` for in-place `void` methods; optional per-language type overrides `lang_types: {java: {return: "List<List<Integer>>"}}` |
| `constraints` | constraint DSL, keyed by param name (§6.1) |
| `guarantees` | free-text promises the DSL can't express ("exactly one answer exists"); forces a custom generator/validator |
| `samples` | `{args: [...], expected: ...}` in canonical JSON |
| `checker` | `exact` \| `unordered` \| `unordered_deep` \| `float:1e-6` \| `custom` |
| `refs` | list of `{lang, file, origin: user\|friend\|llm\|promoted, verified}` |
| `brute` | `{lang, file, origin, verified}` |
| `oracle` | `ref` \| `brute-only` \| `promoted` (derived by the build, stored for display) |
| `limits` | `time_ms: auto\|N` (per test), `memory_mb` |

Schema is versioned (`schema: 1`). After Phase 1 ships, schema changes need a migration, not a breaking edit.

### 4.3 Type system (function mode)

| Spec type | C++ | Java | Python |
|---|---|---|---|
| `int` | `int` | `int` | `int` |
| `long` | `long long` | `long` | `int` |
| `double` | `double` | `double` | `float` |
| `bool` | `bool` | `boolean` | `bool` |
| `char` | `char` | `char` | `str` (len 1) |
| `string` | `string` | `String` | `str` |
| `T[]` (T scalar) | `vector<T>&` | `T[]` | `List[T]` |
| `T[][]` | `vector<vector<T>>&` | `T[][]` | `List[List[T]]` |
| `ListNode` | `ListNode*` | `ListNode` | `Optional[ListNode]` |
| `TreeNode` | `TreeNode*` | `TreeNode` | `Optional[TreeNode]` |

Return types use the same mapping by value (`vector<int>`, not a reference). Java lets the spec override with `List<Integer>` and similar because friends' refs use both styles. Design problems ("implement an LRU cache", a sequence of method calls) are Phase 4 (`mode: ops`).

### 4.4 Canonical I/O
- **Input file:** one JSON value per line, one line per parameter, in signature order. This is exactly LeetCode's custom-testcase format, so cases can be pasted from anywhere.
- **Output:** one JSON value.
- `ListNode` is a JSON array; `TreeNode` is level-order with `null` (LeetCode style); `char` is a one-character string; `long` values beyond 2^53 are still plain JSON integers (parsers must not go through doubles).
- In-place problems (`returns: void` + `output_param: nums`) print that parameter after the call.

## 5. Judge

### 5.1 Stubs and drivers (deterministic templates, never LLM-generated)
- The user's file looks exactly like LeetCode: only `class Solution { ... }`. For C++ the driver supplies `#include <bits/stdc++.h>`, `using namespace std;` and the `ListNode`/`TreeNode` definitions, then `#include "sol.cpp"`.
- **C++:** `g++ -std=c++20 -O2 -pipe`. The JSON parser in the driver is hand-written and minimal (no nlohmann; compile time matters). Binaries are cached by hash of (sol, driver, flags).
- **Java:** `Solution.java` + generated `Main.java`. Run with `-Xss` large and `-Xmx` from `memory_mb`, with the call made on a thread that has a big stack.
- **Python:** generated `driver.py` imports the solution, raises the recursion limit, and runs the call on a `threading` thread with a large stack.

### 5.2 Execution and limits
- **Batch mode.** The driver reads a list of test files, creates a fresh `Solution` per test, times only the method call (monotonic clock), and writes one JSON result line per test. JVM startup and Python imports don't count against TL. This matches LeetCode, including the fact that C++ globals leak between tests; document that in the stub comment.
- If the batch process dies or hits the outer wall timeout, re-run the remaining tests one per process with per-test timeouts, so the verdict lands on the right test.
- **Memory:** use cgroup v2 via `systemd-run --user --scope -p MemoryMax=… -p MemorySwapMax=0` when available. Otherwise measure peak RSS (`getrusage(RUSAGE_CHILDREN).ru_maxrss` or polling `VmHWM`) and flag MLE after the fact. **Never** use `RLIMIT_AS`: it breaks the JVM and ASan.
- **Stack:** raise `RLIMIT_STACK` to unlimited (or a large value) in `preexec_fn` for C++, so a recursive DFS on a 10^5-node line tree behaves as it would on HackerRank and doesn't segfault on Linux's default 8 MB stack.
- **Time limit** `auto`: if a C++ ref exists, `clamp(3 × slowest ref test, 1000 ms, 5000 ms)`; otherwise 1000 ms. Practice-language multipliers are configurable (defaults: Java ×2, Python ×5).

### 5.3 Verdicts
`AC`, `WA`, `TLE`, `MLE`, `RE`, `CE`. Stop at the first failure by default (`--all` runs everything).
- If the failing input is small (under about 2 KB serialized), show input / expected / got with a diff. Otherwise show the test id, sizes and the first differing position.
- Summary line: passed X/Y, max time, max memory, minutes since `dsa open`.
- `--strict` (interview/OA mode): verdict and pass count only.
- `dsa debug` rebuilds C++ with `-g -O0 -fsanitize=address,undefined -D_GLIBCXX_DEBUG` and re-runs the failing test, showing sanitizer output.

## 6. Test generation (no AI involved)

### 6.1 Constraint DSL
Params are listed in signature order and later params may reference earlier ones. Bounds are parsed by the DSL, not by YAML: YAML loaders read `1e5` as a string, so every bound goes through the DSL's own number/expression parser.
```yaml
constraints:
  nums:  {len: [1, 1e5], each: [-1e4, 1e4]}          # + distinct, sorted: asc|desc|nondec|noninc, perm: 0|1
  k:     {range: [1, len(nums)]}                      # expressions: len(), min(), max(), sum(), literals, 1e5 / 10^5
  s:     {len: [1, 2e5], charset: lowercase}          # lowercase|uppercase|letters|digits|alnum|binary|"xyz"
  grid:  {rows: [1, 300], cols: [1, 300], each: "01"}
  root:  {nodes: [0, 1e4], each: [-1000, 1000], shape: any}   # any|bst|balanced|line|complete
  edges: {graph: {n: n, kind: tree, weighted: [1, 1e9], directed: false}}  # tree|connected|dag|any
```

### 6.2 Test plan (per part, configurable)
| Group | Purpose |
|---|---|
| `samples` | from the statement |
| `edge` | minimum sizes, extreme values, all-equal, all-min, all-max |
| `small_random` | ~40 tests small enough for the brute; checks correctness |
| `medium_random` | ~10 |
| `max` | ~6 at maximum size: random, sorted, reverse, all-equal, plus structural worst cases (line tree, star, path graph, alternating signs) |

Seeds are deterministic: `seed = H(slug, part, group, index)`.

### 6.3 Validator
A deterministic validator is derived from the constraints and checks every generated input **and every sample**. A sample that fails validation almost always means a transcription error (OCR reading `10^5` as `105`), so the build stops and points at the constraint line.

### 6.4 Oracles
- **Refs** may be in any language. If several refs exist, they must agree on every test.
- **Brute** (optional, encouraged): an obviously-correct slow solution. **Stress** runs ref against brute on `small_random` plus 300 extra small seeds. Any disagreement stops the build and prints the smallest failing input. This also validates the user's official solutions before they are published.
- **Expected outputs** come from running the ref with a generous timeout (60 s per test); ref speed only matters for TL calibration.
- **Weak-test warning:** if a brute exists and passes every `max` test within TL, warn that the tests don't separate complexities.

### 6.5 Oracle modes (per part)
| Mode | When | Correctness checked on | `max` tests |
|---|---|---|---|
| `ref` | a ref exists and passes samples (and stress, if a brute exists) | all groups | full verdicts |
| `brute-only` | follow-up without a ref | tests small enough for the brute | run-only: only TLE/MLE/RE can fail |
| `promoted` | user's AC solution in brute-only mode, promoted with `dsa promote` | all groups | full verdicts |

In `brute-only` mode, a mismatch is reported as **`WA? (unverified oracle)`** with full input, expected and got, so the user can judge. `dsa dispute` marks the brute as suspect and the part as `needs-review`. Phase 3 adds an optional LLM-written candidate ref, accepted only if it agrees with the brute on every small test (`origin: llm, verified: stress-only`).

### 6.6 stdio mode
For full-program problems (and for exporting to HackerRank/HackerEarth later), a part can use `mode: stdio`. Then:
- an `input_format` template renders the generated params to text;
- outputs are compared token-wise, with float tolerance when the checker says so;
- the same generators, validator and oracles apply.

## 7. Intake (local AI)

### 7.1 Stages
1. **Collect.** Copy raw files to `inbox/<slug>/raw/`, ordered by filename then EXIF/mtime. Originals are kept forever.
2. **Transcribe.**
   - Images go to a local vision model via Ollama, one image per call, with a strict prompt: transcribe verbatim, keep code exactly (indentation matters for Python), write exponents as `10^5` and `≤` as `<=`, and mark unreadable spots as `[??]`. HEIC is converted with `pillow-heif`, and large photos are downscaled.
   - Audio is converted by `ffmpeg` to 16 kHz mono WAV, then transcribed by whisper.cpp (`whisper-cli`). Voice notes may be Hinglish; normalising that is the next stage's job.
   - Text and code files pass through as-is.
   - Output: `transcript.md`, with one section per source.
3. **Structure.** Two focused LLM passes, both JSON-schema constrained (Ollama `format`). Small models do much better on narrow prompts.
   - (a) **Segmentation:** which text belongs to which part, and which code blocks are refs or brutes, in which language, for which part.
   - (b) **Per-part extraction:** title, statement, signature, constraint DSL, samples, checker hint, guarantees.
4. **Review (mandatory).** The draft opens in `$EDITOR` with a checklist header: constraint numbers, signature types, samples, ref-to-part mapping, remaining `[??]`. Validation re-runs on save; loop until valid or abort.
5. **Build.** Run §6. A ref transcribed from a photo must compile and pass samples. If it doesn't, the LLM proposes an OCR-fix diff that is shown for approval and never applied silently.

### 7.2 AI-assisted generation
All of these are execution-verified, retried with error feedback, and capped at N attempts.
- `brute.py` from statement and constraints, when none was provided.
- `gen.py` for `guarantees` or structures the DSL can't express. Interface: `def generate(rng: random.Random, size: str, flavor: str) -> list` (args in signature order). It must pass the validator on 200 samples.
- `checker.py` for problems with multiple valid answers: `def check(args, expected, got) -> tuple[bool, str]`. It must accept ref outputs on all tests and reject 20 mutated outputs.
- Filling the constraint DSL from free-text constraints.

**Never** generate expected outputs or verdicts with the LLM.

### 7.3 Provider abstraction
```python
class LLM(Protocol):
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        images: list[Path] | None = None,
    ) -> str: ...


class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> str: ...
```
- `OllamaLLM` talks to `http://127.0.0.1:11434` and raises at construction if the host is not loopback.
- `WhisperCppTranscriber` shells out to `whisper-cli`.
- `FakeLLM` / `FakeTranscriber` replay recorded responses from `fixtures/ai/` and are used by every default test and in the cloud.
- **4 GB VRAM:** send `keep_alive: 0` after each stage and never hold two models at once.
- Config: `models.text`, `models.vision`, `models.whisper`. Candidates to benchmark first: text `qwen2.5-coder:3b`, `qwen3:4b`; vision `qwen3.5:2b`, `qwen2.5vl:3b`; whisper `small`, `medium`. A 7–8B text model partly offloaded to CPU is acceptable, because intake is a one-time cost per problem.
- `dsa doctor --models` runs each configured model on fixture inputs and reports schema-valid rate and latency.
- Prompts live in `src/dsajudge/ai/prompts/*.md`, never as inline strings.

## 8. Editor integration
- `dsa open` writes the work dir and opens `$DSA_EDITOR` (falling back to `$EDITOR`). VS Code users can set `DSA_EDITOR="code --profile Practice"`.
- It writes `.vscode/tasks.json` with run/submit/debug tasks. README documents nvim keymaps (`:!dsa run`, `:!dsa submit`).
- **Privacy:** VS Code AI extensions (Copilot and similar) upload open files. The README recommends a VS Code profile with no AI extensions, or nvim, and `dsa open` prints a one-line reminder the first time it runs.

## 9. Follow-ups and progress
- Part N+1's statement and stub appear only after part N is AC (`--force` to peek).
- On unlock, the previous part's solution is copied to `prev.<ext>` for reference; the stub uses the new signature.
- A timer starts at `dsa open`. Every `run` and `submit` is logged to SQLite: part, language, verdict, tests passed, elapsed, timestamp.
- Phase 4 adds `dsa stats` and `dsa review` (spaced repetition: re-open solved parts after 3/7/21 days, failed ones sooner).

## 10. Repo layout and stack
```
pyproject.toml              # uv; package dsajudge; console script `dsa`; extra [ai]
src/dsajudge/
  cli.py                    # Typer
  config.py                 # DSA_HOME, config.toml, XDG cache
  model/                    # pydantic schema, type system, constraint DSL parser/evaluator
  lang/                     # cpp.py, java.py, python.py: stub + driver codegen, compile, run
    templates/
  judge/                    # runner, limits, verdicts, checkers, compile cache
  gen/                      # generator library, flavors, validator, test plan, manifest
  oracle/                   # refs agreement, stress, modes, promote, dispute
  intake/                   # collect, transcribe, structure, review
  ai/                       # providers (ollama, whisper, fake), prompts/
  store/                    # bank paths, sqlite
tests/
fixtures/                   # PUBLIC problems only
docs/
```
Stack: Python ≥ 3.11, Typer, pydantic v2, ruamel.yaml (round-trips comments during review), rich, watchfiles, httpx (Ollama), pillow + pillow-heif (in the `ai` extra), pytest, ruff, mypy. The core judge must not depend on AI packages.

## 11. Phases and acceptance criteria

### Phase 0: skeleton
- uv project, `dsa --help`, config loading, `DSA_HOME` resolution.
- `dsa doctor` checks g++, javac/java, python3, ffmpeg, Ollama reachability and the whisper binary. Missing items are warnings, not failures.
- GitHub Actions runs ruff, mypy and pytest.
- **Done when:** `uv run dsa doctor` works in the cloud env and CI is green.

### Phase 1: function-mode judge (C++, Java, Python)
- Schema and loader, type system, canonical JSON I/O.
- Stub and driver generation for every type in §4.3.
- `open`, `run`, `submit`, `debug`, `watch`, `show`, `list`; limits; verdicts; compile cache; part unlocking; timer; attempt log.
- Tests in this phase are the samples plus hand-written cases in `fixtures/problems/<slug>/partN/tests/manual/`. Large cases are produced by a small script in `tests/` (the generator comes in Phase 2).
- Add `fixtures/solutions/max-subarray/` with variants in all three languages: correct, wrong, slow (O(n²)), crash, deep recursion, memory hog.
- **Done when:** each variant gets its expected verdict in each language; part 2 is locked until part 1 is AC; refs in Java and Python run through the same drivers.

### Phase 2: test generation and oracles (no AI)
- Constraint DSL with dependent params; generator library and flavors for all types; auto-validator (samples included); test plan; manifest with seeds and hashes; regeneration cache.
- Multi-ref agreement; brute stress with smallest counterexample; TL calibration; weak-test warning; oracle modes including `brute-only`, `promote` and `dispute`.
- stdio mode.
- `dsa build`, `dsa rebuild`.
- **Done when:** the fixture builds fully (parts 1–2 in `ref` mode, part 3 in `brute-only`). A deliberately wrong ref is caught by stress. A corrupted sample (`10^5` → `105`) is caught by the validator. The weak-test warning fires when TL is set very high.

### Phase 3: local AI intake
- Providers plus fakes with recorded responses; the full intake pipeline and review loop; `followup` and `ref` commands; AI-written brute, gen and checker with verify-and-retry; `doctor --models`.
- Intake fixtures are **synthetic**: statement images are rendered from text with Pillow at test time (including a superscript `10^5` case), and audio uses `FakeTranscriber` with recorded transcripts. Real models are tested only with `pytest -m local` on the user's machine.
- **Done when:** with fakes, `dsa add` on synthetic photo + voice + code inputs produces the expected reviewed `problem.yaml` and a built problem. Locally, `pytest -m local` passes against real Ollama and whisper.cpp.

### Phase 4: polish
- `stats`, `review` queue, design problems (`mode: ops`).
- Export stdio parts as HackerRank/HackerEarth test-case zips.
- Shell completions, PyPI packaging and publishing.

## 12. Testing rules
- Unit tests for the DSL, codegen (golden files), checkers and verdict logic.
- Integration tests compile and run real C++, Java and Python in temp dirs; the toolchains exist in the cloud env.
- No network in tests. No real models in the default run; mark those `@pytest.mark.local`.
- Any test that needs a problem uses `fixtures/` or builds a synthetic one in a temp `DSA_HOME`.

## 13. Decisions log
Append a dated line here whenever a design decision in this file changes, with the reason.
- 2026-10-15: Drivers stream one JSON result line per test over a dedicated fd (passed as argv[1]), so user stdout can't corrupt results and the runner always knows which test is running. A process that stops answering for `TL × 1.5 + 0.5 s` is killed and the running test gets TLE without a re-run. A process that dies is attributed to the running test, which is re-run alone only if earlier tests ran in the same process. With `--all`, the remaining tests continue in a new batch. This refines §5.2's "re-run the remaining tests one per process", which the streaming makes unnecessary.
- 2026-10-15: Phase 1 expected outputs: samples use the yaml; `partN/tests/manual/*.in.json` get theirs by running the refs through the same drivers (refs must agree with each other and with every sample), or with no ref the brute on inputs up to 20 KB (verdicts are `WA?`). Bigger inputs are run-only. Results and C++ ref timings for auto TL are cached in `$XDG_CACHE_HOME/dsajudge/expected/`, keyed by input and source hashes. No `.out.json` files are committed. Phase 2's manifest replaces this.
- 2026-10-15: Without systemd, an RSS watchdog polls `/proc/<pid>/status` every 10 ms and kills the process group above `memory_mb + overhead` (C++ 16 MB, Python 32 MB, Java 192 MB for JVM non-heap; Java's heap is capped by `-Xmx{memory_mb}m`). Per-test VmHWM above the same bound is MLE after the fact. The watchdog also runs under systemd as a second check.
- 2026-10-15: RLIMIT_STACK is raised in the judge process itself (inherited by children) instead of in `preexec_fn`, which is unsafe with the runner's threads.
- 2026-10-15: Python solutions run under the judge's own interpreter (`sys.executable`, ≥ 3.11) by default; `judge.python` in config overrides it.
- 2026-10-15: Added `dsa import DIR`, since `dsa add` arrives in Phase 3. In a source checkout, `dsa open`/`dsa show` copy a public fixture into the bank when the slug is missing, so `DSA_HOME=/tmp/x dsa open max-subarray` works.
- 2026-10-15: Java gets LeetCode's implicit imports (`java.util.*`, `java.util.function.*`, `java.util.stream.*`) prefixed onto line 1 of the copied `Solution.java`, so compiler line numbers still match the user's file. A null `ListNode`/`TreeNode` return serializes as `[]` in every language.
- 2026-10-15: C++ builds use a precompiled `<bits/stdc++.h>` cached in `$XDG_CACHE_HOME/dsajudge/pch/` (one-time ~15 s build, then ~1.3 s compiles instead of ~4 s).
- 2026-10-15: Phase 2 plan configuration lives in `$DSA_HOME/config.toml` under `[plan]` (counts per group, size caps, brute limits), not per part, so `problem.yaml` needed no new test-plan field. The plan is hashed into the manifest's `spec_hash`; `dsa submit` warns when the constraints, a `gen.py`/`validate.py`, or the plan changed since the last build.
- 2026-10-15: The derived oracle mode is stored in `partN/tests/manifest.json`, not written back into `problem.yaml`'s `oracle` field (which stays optional and unused). Build output never edits a file the user authors; only `dsa promote` does, via a comment-preserving append to `refs`.
- 2026-10-15: Seeds use our own RNG layer built on `getrandbits` (§6.2), because `random.shuffle`/`sample`/`randrange` algorithms are not stable across Python releases. Custom `gen.py` receives a plain `random.Random`; the manifest's input hashes catch any drift.
- 2026-10-15: Stored tests are the `edge`, `small_random` and `medium_random` groups whose input is at most 64 KB (`tests/small/<group>-<NN>.in.json` and `.out.json`); `max` tests are regenerated. A regenerated test's answer comes from the first ref via the expected-output cache and is checked against the manifest's `expected_sha256`. Failed ref/brute runs are not cached (they depend on timeouts and load).
- 2026-10-15: Brute-only parts: the brute answers the samples (which it must match) plus every `edge`/`small_random` test and the `medium_random` tests it finishes within its timeout (a brute that crashes or times out on edge/small tests is a build error). `max` tests carry no answer. `dsa dispute` writes `partN/tests/disputes.json`; disputed tests are run-only and the part shows `needs-review`.
- 2026-10-15: `dsa promote` requires an `AC` `submit` logged with the same code hash as the file being promoted, then rebuilds with the file as a ref (`origin: promoted`). If the rebuild fails (for example the promoted solution disagrees with the brute in stress), `problem.yaml` and the ref file are restored.
- 2026-10-15: Stress runs the first ref against the brute on `edge`, `small_random`, the samples and 300 extra seeds. A mismatch is shrunk (shorter arrays, parallel arrays together, smaller numbers, fewer rows/columns, shorter strings), keeping only candidates that still satisfy the constraints and still disagree.
- 2026-10-15: Weak-test check (§6.4) runs the brute on the `max` tests with the limit for its language, waiting at most `plan.weak_check_cap_s` (20 s). It warns when the brute passes them all, and also when the limit is longer than the check can wait (so a very high `time_ms` always warns).
- 2026-10-15: stdio mode needs a `signature` (params and `returns` define the generated input and expected output; the method name is ignored) and an `input_format`; `Part.output_format` (default `{result}`) is a new optional field, which is an additive schema change. Expected output is stored as normalized text and compared token-wise. Time is wall clock minus a fixed per-language start-up cost (Java 250 ms, Python 40 ms). stdio Java solutions are `public class Solution` with `main`.
- 2026-10-15: An optional `partN/validate.py` (`validate(args) -> str | None`) is supported next to `gen.py` for guarantees the DSL cannot express. `gen.py` and `validate.py` run in a separate `python -I` interpreter, never in the judge process.
- 2026-10-15: `checker: custom` (`checker.py`) stays unimplemented and moves to Phase 3 with the AI-written checkers it is verified against.
- 2026-10-15: Per-test memory is read from the child's own `VmHWM` (the C++ driver now does what the Python and Java drivers already did; stdio programs are measured by the RSS watchdog). `ru_maxrss` is never used for judging: Linux carries the parent's peak RSS into a child across exec, so a large judge process (`dsa build` holds big inputs in memory) turned small memory limits into false MLEs. This corrects the Phase 1 C++ driver.
- 2026-10-15: Phase 3 providers: `OllamaLLM` uses httpx with `trust_env=False` (problem content never goes through a configured proxy), refuses non-loopback hosts at construction and sends `keep_alive: 0`. Commands get providers from `ai.factory.PROVIDERS` (`LocalProviders`, or `FixedProviders` with a `FakeLLM` in tests), so nothing but that module names a concrete provider. New `[ai]` settings: `whisper_model_dir`, `whisper_language`, `timeout_s`, `num_ctx`, `max_attempts` (default 3, caps every retry loop) and `max_image_side`.
- 2026-10-15: Every prompt file starts its rendered system prompt with `[task: <file name>]`. `FakeLLM` matches recorded responses on that tag, optional text in the prompt and optional image file name, hands out `responses` in order, and fails loudly when they run out, so retry loops are testable and a runaway loop cannot pass.
- 2026-10-15: Code is never retyped by the model. Segmentation refers to a code block by `(section, block)` and the text comes verbatim from the transcript, so Python indentation survives; for code files the language comes from the extension, not the model.
- 2026-10-15: The draft under review is a normal problem directory at `inbox/<key>/draft/` (for `followup`, a copy of the existing problem plus the new part, published only after the new part builds). `problem.yaml` gets a regenerated checklist header (constraints with evaluated bounds, signature, samples, ref/brute per part, assistant notes, blocking errors). Review is mandatory: no `$EDITOR` means no build; remaining `[??]`, constraint errors, sample violations or a part with neither ref nor brute reopen the editor; emptying the file aborts and restores the draft; `--resume` skips every finished stage.
- 2026-10-15: A part with neither ref nor brute gets an AI brute, accepted only if it reproduces every example (and, with a ref, agrees with it on 60 generated inputs, recorded as `verified: stress-only`). `gen.py` must pass the validator on 200 inputs and any `validate.py`; `checker.py` must accept the oracle's outputs on samples plus generated inputs and reject 20 mutated outputs. Failures go back to the model with the reason; after `ai.max_attempts` the part is left for the reviewer with a note. The optional LLM-written candidate ref (§6.5) is not built.
- 2026-10-15: `checker: custom` now works (moved here from Phase 2): `partN/checker.py` runs in one long-lived `python -I` worker per checker; a crash, hang (10 s) or malformed reply is a judge error, never a verdict. Every comparison site passes the test's input to the checker, and the checker's reason is shown on WA.
- 2026-10-15: Code transcribed from a photo or voice note that does not compile or pass the samples gets a model-proposed fix, shown as a unified diff and applied only after confirmation; code supplied as a file is never auto-fixed. `dsa ref` adds a ref (default `origin: friend`) through the same check, then a full build (ref agreement and stress); on any failure the yaml and files are restored.
- 2026-10-15: `dsa doctor --models` runs 3 repetitions of: vision OCR of a Pillow-rendered statement (must keep `10^5`, `nums`, `target`, the example), text segmentation, extraction and constraint filling (schema-valid and semantically usable), and a whisper.cpp smoke run on silence (accuracy is not measured: there is no speech fixture in the repo).
- 2026-10-15: The test suite blocks non-loopback sockets in every test.
