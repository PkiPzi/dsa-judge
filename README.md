# dsa-judge

A local-first DSA practice judge.

- **Add questions from photos, typed text or voice notes.** A local model transcribes and structures them, and you review the result.
- **Hidden tests are built automatically** from your reference solution (C++, Java or Python) and the constraints.
- **Solve LeetCode-style** in nvim or VS Code with `dsa run` and `dsa submit`.
- **Follow-ups unlock one part at a time.**

Nothing leaves your machine: AI runs on Ollama and whisper.cpp on localhost, and your problems live in `~/dsa-bank`, outside this repo.

> Status: Phases 0-3 (skeleton, the function-mode judge, test generation with oracles, and local AI intake). Phase 3's real-model paths are only exercised on your machine: see [LOCAL_TESTING.md](LOCAL_TESTING.md). See [docs/PLAN.md](docs/PLAN.md) for the design and [docs/PROMPTS.md](docs/PROMPTS.md) for the build prompts.

## Local setup (EndeavourOS / Arch)

```bash
# toolchains for the judge
sudo pacman -S --needed gcc jdk-openjdk python uv

# intake (needed from Phase 3)
sudo pacman -S --needed ffmpeg wl-clipboard ollama-cuda
sudo systemctl enable --now ollama
# whisper.cpp from the AUR; pick the CUDA build if one is offered
yay -Ss whisper.cpp

# then, in this repo
uv sync --all-extras
uv run dsa doctor

# put `dsa` on your PATH (editor tasks and :!dsa run need it)
uv tool install --editable .
```

## Adding questions

```bash
dsa add statement.jpg voice-note.m4a friend.cpp --slug two-pass   # any mix; --clip / --text also work
dsa followup two-pass later-photo.jpg                               # appends part N+1
dsa ref two-pass friend2.java --part 2                              # attach another reference solution
dsa doctor --models                                                 # which local models are usable
```

`dsa add` runs collect, transcribe (photos on a local vision model, voice on whisper.cpp), a
segmentation pass and a per-part extraction pass (both JSON-schema constrained), the constraint
DSL, and writes missing pieces (a brute-force solution; `gen.py` for guarantees; `checker.py`
for problems with several valid answers). Every AI-written file is **verified by execution** and
retried with the error fed back. Then `$EDITOR` opens `problem.yaml` with a checklist header
(constraint numbers, signature types, samples, which code is a ref or brute for which part, any
`[??]`); the review repeats until the draft validates and nothing is unreadable. Only then are
the hidden tests built. Code from a photo that doesn't compile or pass the samples gets a
proposed fix, shown as a diff, which is applied only if you confirm. Everything goes through the
inbox (`$DSA_HOME/inbox/NAME/`), so `dsa add --resume NAME` continues after a crash.

## Practice loop

```bash
dsa open max-subarray          # creates $DSA_HOME/work/max-subarray/part1 and opens your editor
cd ~/dsa-bank/work/max-subarray/part1
dsa run                        # samples
dsa run --case '[1,-2,3]'      # plus a custom case, LeetCode's one-JSON-value-per-line format
dsa submit                     # full suite; AC unlocks the next part
dsa submit --strict            # interview mode: verdict and pass count only
dsa debug                      # re-run the last failing test (C++: ASan/UBSan, _GLIBCXX_DEBUG)
dsa watch                      # re-run the samples on every save
dsa show                       # parts, oracle, hidden tests, status, best times
```

## Building hidden tests

```bash
dsa build max-subarray            # generate tests, check refs, stress against the brute, set the TL
dsa build max-subarray --part 2   # one part
dsa rebuild max-subarray          # wipe generated tests and caches, build again from the seeds
```

`dsa submit` judges against what `dsa build` made (and says so if nothing was built). Tests come
from the constraints in `problem.yaml` plus a reference solution: see
[docs/CONSTRAINTS.md](docs/CONSTRAINTS.md) for the constraint language, the test plan, oracle modes
and stdio mode. A build stops with an explanation when a sample breaks its constraints, a ref
fails or disagrees with another ref, or a ref and the brute disagree (it prints the smallest
failing input).

Parts with a brute but no ref are **brute-only**: small tests are judged against the brute and a
mismatch shows as `WA? (unverified oracle)`; the biggest tests only check time, memory and
crashes. When you think the brute is wrong:

```bash
dsa dispute small_random/07 --note "brute misses the wrap-around"   # makes that test run-only
dsa promote                       # after your AC: your solution becomes the ref, with full verdicts
```

Your file holds only `class Solution { ... }`, exactly like LeetCode. The judge supplies headers
(`<bits/stdc++.h>`, `using namespace std;`), `ListNode`/`TreeNode`, Java's `java.util.*` imports
and Python's `typing`/`collections`/`heapq` names. Use `--lang java` or `--lang python` with
`dsa open` to practice in another language; `default_lang` in `$DSA_HOME/config.toml` changes
the default.

In a source checkout, `dsa open max-subarray` copies the public fixture into your bank the
first time. `dsa import DIR` copies any problem directory in.

### nvim

```lua
-- ~/.config/nvim/lua/config/keymaps.lua (or wherever you keep keymaps)
vim.keymap.set("n", "<leader>dr", "<cmd>w | split | terminal dsa run<cr>", { desc = "dsa run" })
vim.keymap.set("n", "<leader>ds", "<cmd>w | split | terminal dsa submit<cr>", { desc = "dsa submit" })
vim.keymap.set("n", "<leader>dd", "<cmd>w | split | terminal dsa debug<cr>", { desc = "dsa debug" })
```
Or keep `dsa watch` running in a second terminal. Plain `:!dsa run` works too.

### VS Code

`dsa open` writes `.vscode/tasks.json` in the work dir: **Run Build Task** (Ctrl+Shift+B) runs
`dsa run`, **Run Test Task** runs `dsa submit`, and compile errors show up in the Problems
panel. Open the work dir itself with `DSA_EDITOR="code --profile Practice"` so the tasks load.

## Configuration (`$DSA_HOME/config.toml`, all optional)

```toml
default_lang = "cpp"          # cpp | java | python
dsa_command = "dsa"           # used in generated editor tasks

[ai]                          # intake (Phase 3); Ollama must stay on loopback
ollama_url = "http://127.0.0.1:11434"
[ai.models]
text = "qwen2.5-coder:3b"
vision = "qwen2.5vl:3b"
whisper = "small"

[plan]                        # hidden-test plan (dsa build)
small_random = 40
medium_random = 10
max = 6
small_cap = 8                 # longest array in a "small" test

[judge]
memory_backend = "auto"       # auto | systemd | poll
time_multipliers = { cpp = 1.0, java = 2.0, python = 5.0 }
cxx = "g++"
python = ""                   # empty: the judge's own interpreter
```

Memory limits use a `systemd-run --user --scope` cgroup with `MemoryMax` when that works, and
otherwise an RSS watchdog that polls `/proc` and kills the process group. `dsa doctor` shows
which one is active.

## Privacy checklist
- Keep `DSA_HOME` outside any git repo with a remote.
- VS Code AI extensions (Copilot and similar) upload open files. Practice in nvim, or in a VS Code profile with no AI extensions (`DSA_EDITOR="code --profile Practice"`).
- Ollama listens on 127.0.0.1 by default; leave it that way.
