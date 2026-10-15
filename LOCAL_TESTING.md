# Testing the AI intake on your machine

Everything in the default test suite uses recorded responses (`FakeLLM`, `FakeTranscriber`), so it
passes without a GPU. This page is for checking the **real** models on EndeavourOS with the
GTX 1650 (4 GB VRAM). Nothing here runs automatically and nothing leaves your machine: Ollama is
used on `127.0.0.1` and whisper.cpp is a local process.

> I could not run any of this in the cloud build environment (no GPU, no Ollama). Package and model
> names below are the ones the plan lists; if one is wrong or renamed, the tool tells you (for a
> missing model: `ollama pull <name>`), and `dsa doctor --models` shows what actually works.

## 1. Install

```zsh
# Ollama with CUDA support
sudo pacman -S --needed ollama-cuda ffmpeg wl-clipboard
sudo systemctl enable --now ollama

# whisper.cpp from the AUR. Pick the CUDA (or Vulkan) build if one is offered.
yay -Ss whisper.cpp
yay -S whisper.cpp-cuda          # name may differ: use whatever `yay -Ss` shows
which whisper-cli                # dsa looks for whisper-cli, whisper-cpp or whisper.cpp
```

A 4 GB card holds one small model at a time. dsa already unloads every model after each call
(`keep_alive: 0`), but also tell the server to never keep two:

```zsh
sudo systemctl edit ollama
# add:
#   [Service]
#   Environment="OLLAMA_MAX_LOADED_MODELS=1"
#   Environment="OLLAMA_NUM_PARALLEL=1"
sudo systemctl restart ollama
```

## 2. Pull the models

The defaults are in `config.py` (`models.text`, `models.vision`, `models.whisper`). Candidates from
the plan, smallest first:

```zsh
ollama pull qwen2.5-coder:3b     # text (default): structuring, briefs, checkers
ollama pull qwen3:4b             # text alternative
ollama pull qwen2.5vl:3b         # vision (default): photos of statements
ollama pull qwen3.5:2b           # vision alternative (check the tag exists in the Ollama library)
```

whisper.cpp needs a ggml model file. Either take one from an AUR model package
(`yay -Ss whisper.cpp-model`) or download it with the script in the whisper.cpp repository:

```zsh
mkdir -p ~/.local/share/whisper.cpp && cd ~/.local/share/whisper.cpp
# from a whisper.cpp checkout:  ./models/download-ggml-model.sh small
# or fetch ggml-small.bin by hand and put it here
ls ggml-small.bin
```

dsa looks for `ggml-<name>.bin` in `ai.whisper_model_dir`, `~/.local/share/whisper.cpp/`,
`~/whisper.cpp/models/` and the usual `/usr/share/whisper.cpp*` directories. Voice notes in Hinglish
usually do better with `medium` (partly on CPU is fine) than `small`; try `ai.whisper_language =
"hi"` or `"en"` if `auto` guesses wrong.

Put non-default choices in `~/dsa-bank/config.toml`:

```toml
[ai]
whisper_language = "auto"
[ai.models]
text = "qwen3:4b"
vision = "qwen2.5vl:3b"
whisper = "medium"
```

## 3. Check what works

```zsh
uv sync --all-extras
uv run dsa doctor               # ffmpeg, whisper-cli and Ollama show up as ok
uv run dsa doctor --models      # runs every configured model on fixture inputs
```

`--models` prints, per check, how many of 3 runs were **usable** (schema-valid and sane), the
average time, and a note. Roughly what to expect and do:

| Check | Usable means | If it is below 3/3 |
|---|---|---|
| `vision:ocr` | the rendered statement comes back with `10^5`, `nums`, `target` and the example | try the other vision model; `10^5` read as `105` is the classic slip (the validator catches it later, but fewer slips is better) |
| `text:segment` | valid JSON with parts numbered 1, 2, ... | try a larger text model |
| `text:extract` | a signature with 2 params and a sample that type-checks | same |
| `text:constraints` | constraints that parse and accept the sample | same |
| `whisper:runs` | `whisper-cli` loads the model and runs on 2 s of silence | accuracy is not measured; see section 4 |

A 7-8B text model that spills partly onto the CPU is fine: intake is a one-time cost per problem.

## 4. Run the real-model tests

```zsh
uv run pytest -m local -v -rs
```

| Test | What it proves |
|---|---|
| `test_vision_model_reads_a_rendered_statement` | the vision model transcribes a Pillow-rendered statement, keeping the superscript exponent |
| `test_text_model_structures_a_statement` | at least 2 of 3 runs of segmentation, extraction and constraint filling are usable |
| `test_whisper_cpp_runs` | `whisper-cli` + ffmpeg + your model file work end to end |
| `test_the_whole_intake_with_real_models` | `dsa add` on a rendered photo plus a Python file reaches a draft or fails with a clear message |

Tests skip (with the reason) when Ollama, a model or whisper-cli is missing, so a partial setup is
fine. Please also try one real voice note by hand, since that is the part no test can cover:

```zsh
export DSA_HOME=/tmp/dsa-local
dsa add statement.jpg voice-note.m4a solution.cpp --slug try-1
# review opens $EDITOR with a checklist; fix anything wrong, save, quit
dsa open try-1
```

## 5. If something fails

| Symptom | Fix |
|---|---|
| `Ollama is not reachable at http://127.0.0.1:11434` | `systemctl status ollama`; do not point `ai.ollama_url` at a non-loopback host (dsa refuses) |
| `model 'x' is not installed` | `ollama pull x`, or change `ai.models.*` |
| first call is very slow | the model is loading; every call unloads it again by design |
| out-of-memory in Ollama | use a smaller vision/text model; `nvidia-smi` should show one model at a time |
| `whisper model 'small' not found` | put `ggml-small.bin` in `ai.whisper_model_dir` |
| the draft keeps failing review | the checklist header names the problem; an emptied `problem.yaml` aborts, and `dsa add --resume NAME` continues from the saved inbox |
