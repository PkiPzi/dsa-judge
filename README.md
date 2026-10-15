# dsa-judge

A local-first DSA practice judge.

- **Add questions from photos, typed text or voice notes.** A local model transcribes and structures them, and you review the result.
- **Hidden tests are built automatically** from your reference solution (C++, Java or Python) and the constraints.
- **Solve LeetCode-style** in nvim or VS Code with `dsa run` and `dsa submit`.
- **Follow-ups unlock one part at a time.**

Nothing leaves your machine: AI runs on Ollama and whisper.cpp on localhost, and your problems live in `~/dsa-bank`, outside this repo.

> Status: under construction. See [docs/PLAN.md](docs/PLAN.md) for the design and [docs/PROMPTS.md](docs/PROMPTS.md) for the build prompts.

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
```

## Privacy checklist
- Keep `DSA_HOME` outside any git repo with a remote.
- VS Code AI extensions (Copilot and similar) upload open files. Practice in nvim, or in a VS Code profile with no AI extensions (`DSA_EDITOR="code --profile Practice"`).
- Ollama listens on 127.0.0.1 by default; leave it that way.
