# Setup and debugging

How to get `dsa` running on your machine, and what to do when something does not behave.
Written for EndeavourOS/Arch with zsh, but nothing here is Arch-specific except the package names.
Real-model (Ollama, whisper.cpp) setup is in [LOCAL_TESTING.md](../LOCAL_TESTING.md); releasing is in
[RELEASING.md](RELEASING.md).

## 1. Setup

### Toolchains

```zsh
sudo pacman -S --needed gcc jdk-openjdk python uv     # the judge: C++, Java, Python
sudo pacman -S --needed ffmpeg wl-clipboard           # intake: audio conversion, `dsa add --clip`
```

You only need the compilers for the languages you practise in, but `dsa build` of a problem with
refs in three languages (the `min-stack` sample) needs all three.

### Install `dsa`

From PyPI (once published, see RELEASING.md):

```zsh
uv tool install dsajudge              # add 'dsajudge[ai]' for photo and voice intake
```

From a checkout (for development, or to try an unreleased branch):

```zsh
git clone https://github.com/PkiPzi/dsa-judge && cd dsa-judge
uv sync --all-extras
uv tool install --editable .          # puts `dsa` on your PATH (editor tasks and :!dsa need it)
```

Then check the machine:

```zsh
dsa doctor
```

`ok` means that feature will work; `warn` means it will not until you fix it, and is harmless if
you do not use that feature (`ollama` and `whisper.cpp` only matter for `dsa add`). Things worth
knowing in the output:

| Line | Meaning |
|---|---|
| `memory limits  poll` | no `systemd-run --user` (common over ssh, containers, some desktops): memory limits use an RSS watchdog instead of a cgroup. Fine for practice. |
| `memory limits  systemd` | cgroup limits are active. |
| `stack limit` | the judge raised `RLIMIT_STACK`, so deep recursion behaves like on HackerRank. |
| `DSA_HOME` | where your problem bank lives (created on first use). Keep it out of any git repo with a remote. |

### Where things live

```
~/dsa-bank/                         $DSA_HOME, override with the environment variable
  config.toml                       optional settings (see README)
  db.sqlite                         attempts, timers, reviews
  problems/<slug>/                  problem.yaml, statements, refs, brute, tests/ (built)
  work/<slug>/partN/                your code: sol.cpp | Solution.java | sol.py, statement.md,
                                    .dsa.json, .vscode/tasks.json, history/ (older reviews)
  inbox/<name>/                     intake scratch for `dsa add --resume <name>`
  export/<slug>/                    zips from `dsa export`
~/.cache/dsajudge/                  $XDG_CACHE_HOME/dsajudge: compiled binaries, regenerated
                                    large tests, expected-output cache, precompiled header
```

Everything under `~/.cache/dsajudge` is disposable. Nothing in either directory is ever uploaded:
the only network access is to Ollama on `127.0.0.1`.

### Editors

nvim: `dsa open SLUG` opens your `$EDITOR`; add the keymaps from the README (`<leader>dr` run,
`<leader>ds` submit, `<leader>dd` debug), or keep `dsa watch` running in a second terminal.

VS Code: `dsa open` writes `.vscode/tasks.json` in the work directory. Open that directory
itself (`DSA_EDITOR="code --profile Practice" dsa open SLUG`), then Ctrl+Shift+B runs `dsa run`,
the test task runs `dsa submit`, and compile errors land in the Problems panel. Use a profile
with no AI extensions: they upload open files.

If `dsa` is not on VS Code's `PATH`, set `dsa_command = "/full/path/to/dsa"` in `config.toml`.

### Shell completion

```zsh
dsa completions zsh --install         # writes ~/.local/share/zsh/site-functions/_dsa
```

Add the `fpath=(...)` line it prints to `~/.zshrc` before `compinit`, and open a new shell.
bash and fish work the same way (`dsa completions bash --install`). No rc file is edited for you.

### First run

```zsh
dsa open max-subarray --lang python   # copies the bundled sample problem into your bank
cd ~/dsa-bank/work/max-subarray/part1
dsa run && dsa submit
```

## 2. Debugging your solution

| Command | Use |
|---|---|
| `dsa run` | the samples (all of them, with expected vs got) |
| `dsa run --case '<json>'` | plus your own input: one JSON value per line, in signature order. Design problems take two lines, the operations and one argument list per operation |
| `dsa run --case-file F` | the same from a file |
| `dsa submit` | the hidden suite; stops at the first failure |
| `dsa submit --all` | every test, so you see how many fail and which |
| `dsa debug [TEST_ID]` | re-run a failing test (default: the last one) |
| `dsa watch` | re-run the samples on every save |
| `dsa show` | parts, oracle mode, status, best times |

`dsa debug` rebuilds C++ with `-g -O0 -fsanitize=address,undefined -D_GLIBCXX_DEBUG` and shows
the sanitizer report (out-of-bounds, use after free, signed overflow, uninitialised reads).
For Java and Python it shows the full stack trace. Test ids tab-complete inside a work directory.

Reading a verdict:

| Verdict | What to look at |
|---|---|
| `Wrong Answer` | the input, expected and got are printed when small. For a design problem the message names the call: `call #7 getMin() returned -3, expected -2`. For a large input you get the test id, sizes and the first differing position; `dsa debug TEST_ID` reproduces it. |
| `WA? (unverified oracle)` | the expected answer came from a brute force, not a reference solution. You may be right: `dsa dispute TEST_ID`. |
| `Time Limit Exceeded` | the limit is per test, in the language you are using (Java x2, Python x5 of the C++ limit). Only the time inside your method counts, not JVM start-up or imports. |
| `Memory Limit Exceeded` | peak resident memory over the limit. With the `poll` backend the process is killed when it crosses the limit. |
| `Runtime Error` | exit code or signal, stderr tail, and for exceptions the test and call that raised it. |
| `Compile Error` | compiler output with line numbers pointing at your file. |
| `Judge Error` | not your fault: a test input or an oracle is broken. See below. |

C++ note, as on LeetCode: globals and statics persist from one test to the next inside one run,
so reset them in your method.

## 3. Debugging the judge and the bank

### "hidden tests are not built yet" or "out of date"

```zsh
dsa build SLUG            # (re)generate tests, check refs, stress against the brute, set the TL
dsa rebuild SLUG          # wipe generated tests and caches, build again from the seeds
```

Run `dsa build` after editing constraints, a generator, a ref or the plan in `config.toml`.
`dsa submit` warns when the suite no longer matches the problem.

### A build fails

The message says what to fix; the usual ones:

| Message | Cause |
|---|---|
| `sample N breaks the constraints` | usually a transcription slip (`10^5` read as `105`); fix the constraint or the sample at the `problem.yaml` line it prints |
| `ref ... disagrees with sample/N` | the ref is wrong, or the sample was transcribed wrong |
| `refs disagree on TEST` | two reference solutions give different answers on that test |
| `stress: ref and brute disagree` | the smallest counterexample is printed; one of the two is wrong (design problems show it as a short list of calls) |
| `weak tests: the brute passes every max test` | raise the maximum sizes or lower `limits.time_ms` |
| `has guarantees ... write partN/gen.py` | promises the constraint language cannot express need a custom generator |
| `stored test ... was modified; run dsa rebuild` | someone edited `partN/tests/`; rebuild |

### Odd failures that are not your code

- **First C++ compile takes about 15 s**: it builds a precompiled `<bits/stdc++.h>` once (set
  `judge.use_pch = false` in `config.toml` to skip it).
- **Stale binaries or answers**: delete the cache, it is rebuilt on demand:
  `rm -rf ~/.cache/dsajudge` (or only `build/`, `expected/`, `tests/`).
- **Everything says MLE or TLE on a busy machine**: close heavy programs and rerun; limits are
  wall-clock for the method call. Set `judge.memory_backend = "systemd"` or `"poll"` to force one
  mechanism if `auto` picks badly.
- **`javac` or `g++` not found**: set `judge.javac`, `judge.java`, `judge.cxx` in `config.toml`.
- **Python solutions use the wrong interpreter**: they run under the judge's own Python (3.11+);
  set `judge.python = "/usr/bin/python3"` to change it.
- **A test works with `run --case` but fails in `submit`**: `submit` runs all tests in one process
  per batch, so state you keep in globals leaks between tests.

### Intake (`dsa add`)

```zsh
dsa doctor --models           # which local models are usable, with timings
dsa add --resume NAME         # continue after a crash or Ctrl-C (NAME is the inbox directory)
```

Every stage writes into `~/dsa-bank/inbox/NAME/` (`transcripts/`, `transcript.md`,
`segments.json`, `extraction/`, `draft/`), so you can read what the model produced at each step.
If a stage came out wrong, delete that stage's file and resume. Photos of one question are
transcribed one by one and then stitched; `transcripts/NN.md` holds the untouched per-photo text
and the review notes list what was joined. Code from a photo that does not compile or pass the
samples gets a proposed fix shown as a diff, applied only if you confirm.

Ollama unreachable: `systemctl status ollama`, and `ai.ollama_url` must stay on loopback (the
tool refuses anything else). Model missing: `ollama pull <name>`.

### Reviews, stats and exports

- `dsa review` empty? Reviews start 3 days after your first *accepted* `submit`.
- A review left open for 7 days counts as failed; `dsa review fail` gives up explicitly.
- `dsa export` skips tests with no answer (brute-only max tests) and tests a platform would
  reject; the `SLUG-partN-FORMAT.notes.txt` file next to the zip lists each skipped test and why.
- Attempts live in `db.sqlite` (`sqlite3 ~/dsa-bank/db.sqlite 'select * from attempts'`). Back
  it up if your history matters.

### Running the project's own tests

```zsh
uv run pytest                   # default suite: no models, no network (about 10 minutes: it
                                # compiles and runs real C++, Java and Python)
uv run pytest tests/test_ops.py -x -q
uv run pytest -m local          # needs Ollama + whisper.cpp
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Tests use a temporary `DSA_HOME` and never touch `~/dsa-bank`. Codegen output is pinned by golden
files; after an intentional template change run `DSA_UPDATE_GOLDEN=1 uv run pytest
tests/test_codegen_golden.py` and review the diff.

### Reporting a problem

Include `dsa --version`, the `dsa doctor` output, the exact command and its output. Do not paste
real interview questions into an issue: reproduce with one of the bundled sample problems.
