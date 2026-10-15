# dsa-judge

A local-first DSA practice judge.

- **Add questions from photos, typed text or voice notes.** A local model transcribes and structures them, and you review the result.
- **Hidden tests are built automatically** from your reference solution (C++, Java or Python) and the constraints.
- **Solve LeetCode-style** in nvim or VS Code with `dsa run` and `dsa submit`.
- **Follow-ups unlock one part at a time.**

Nothing leaves your machine: AI runs on Ollama and whisper.cpp on localhost, and your problems live in `~/dsa-bank`, outside this repo.

> Status: Phase 0 + 1 (skeleton and the function-mode judge for C++, Java and Python). Test generation (Phase 2) and AI intake (Phase 3) are not built yet. See [docs/PLAN.md](docs/PLAN.md) for the design and [docs/PROMPTS.md](docs/PROMPTS.md) for the build prompts.

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
dsa show                       # parts, status, best times
```

Your file holds only `class Solution { ... }`, exactly like LeetCode. The judge supplies headers
(`<bits/stdc++.h>`, `using namespace std;`), `ListNode`/`TreeNode`, Java's `java.util.*` imports
and Python's `typing`/`collections`/`heapq` names. Use `--lang java` or `--lang python` with
`dsa open` to practice in another language; `default_lang` in `$DSA_HOME/config.toml` changes
the default.

In a source checkout, `dsa open max-subarray` copies the public fixture into your bank the
first time. `dsa import DIR` copies any problem directory in. (`dsa add` arrives in Phase 3.)

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
