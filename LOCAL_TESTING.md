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
sudo pacman -S --needed ollama-cuda ffmpeg wl-clipboard kdialog
sudo systemctl enable --now ollama

# whisper.cpp with the CUDA backend, both from the official repos (no AUR needed)
sudo pacman -S --needed whisper-cpp ggml-cuda
which whisper-cli                # dsa looks for whisper-cli, whisper-cpp or whisper.cpp
ls /usr/lib/ggml/libggml-cuda.so # the GPU backend whisper-cli loads at start-up
```

Models can live on another partition (they are large): set `OLLAMA_MODELS=/path` for the Ollama
service with `sudo systemctl edit ollama`, and point `ai.whisper_model_dir` at a directory there
too (section 2). Make sure the service user can read the Ollama path.

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

whisper.cpp needs a ggml model file. On a 4 GB card the quantized large-v3-turbo model (about
574 MB) fits next to the desktop and is far more accurate than `small`. Download it into
`ai.whisper_model_dir`:

```zsh
mkdir -p /mnt/models/whisper && cd /mnt/models/whisper     # any directory you own
curl -L -C - -O https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
ls -l ggml-large-v3-turbo-q5_0.bin   # about 574 MB; a few KB means an HTML error page
```

dsa looks for `ggml-<name>.bin` in `ai.whisper_model_dir`, `~/.local/share/whisper.cpp/`,
`~/whisper.cpp/models/` and the usual `/usr/share/whisper.cpp*` directories.

Check that the GPU is really used, with any short voice note. The first lines of the
`whisper-cli` log must show the CUDA device and backend; if they are missing it silently runs on
the CPU, many times slower:

```zsh
ffmpeg -y -i note.m4a -ar 16000 -ac 1 /tmp/note.wav
whisper-cli -m /mnt/models/whisper/ggml-large-v3-turbo-q5_0.bin -f /tmp/note.wav -l hi -nt 2>&1 \
  | grep -iE "cuda|backend|gpu"
# expect:  ggml_cuda_init: found 1 CUDA devices ...
#          whisper_backend_init_gpu: using CUDA0 backend
```

The "suboptimal performance due to a lack of tensor cores" line is normal on a GTX 1650.

Language matters for Hinglish voice notes: `auto` usually detects English and drops the Hindi
sentences, so use `hi`. whisper writes Hindi in Devanagari, which many terminals render badly, so
`ai.whisper_prompt` (a romanized Hinglish sample, on by default) steers it to Latin letters; set it
to `""` to get Devanagari back. `ai.whisper_beam_size` defaults to 1 (greedy, about 4x faster than
whisper.cpp's beam of 5 on a GTX 1650); raise it if accuracy matters more than speed.

Put non-default choices in `~/dsa-bank/config.toml`:

```toml
[ai]
whisper_model_dir = "/mnt/models/whisper"
whisper_language = "hi"
[ai.models]
text = "qwen2.5-coder:3b"
vision = "qwen2.5vl:3b"
whisper = "large-v3-turbo-q5_0"
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
# or run `dsa add --slug try-1` alone: a file dialog opens (needs kdialog) where you
# Ctrl/Shift-click the photos, voice note and solution photo or file
# review opens $EDITOR with a checklist; fix anything wrong, save, quit
dsa open try-1
```

### Many questions in one go: `dsa bundle`

A set of photos that holds several different questions (each with its statement and solution) is
added with one command; `dsa add` is for one question.

```zsh
dsa bundle                          # a file dialog opens: select all the photos
dsa bundle --name week1 --unattended *.jpeg
```

What it does: transcribes every photo (code is fenced even when the vision model forgets), drops
repeated pages, asks the text model where each new question starts, then builds every question
through the normal pipeline, one inbox per question (`week1-q01`, `week1-q02`, ...). A question
that fails never stops the others; `dsa bundle --resume week1` redoes only what is not built.
Photos named `x.jpeg`, `x (1).jpeg`, `x (2).jpeg` (WhatsApp) are taken in that order.

`--unattended` skips the editor review but keeps the same gate: a draft is accepted only if it
validates and has no `[??]` left; anything else stays pending for `dsa add --resume week1-q03`.
Accepted drafts were **not checked by a human**, so skim them (`dsa show`, the statement) before
trusting their hidden tests. Strings with no stated alphabet are assumed lowercase, and the
problem's notes say so.

## 5. If something fails

| Symptom | Fix |
|---|---|
| `Ollama is not reachable at http://127.0.0.1:11434` | `systemctl status ollama`; do not point `ai.ollama_url` at a non-loopback host (dsa refuses) |
| `model 'x' is not installed` | `ollama pull x`, or change `ai.models.*` |
| first call is very slow | the model is loading; every call unloads it again by design |
| out-of-memory in Ollama | use a smaller vision/text model; `nvidia-smi` should show one model at a time |
| intake is slow (vision takes minutes) | the desktop shares the 4 GB card, so a 3B model is only partly on the GPU: `journalctl -u ollama \| grep offloaded` shows e.g. `19/37 layers`. Close Firefox and VS Code windows before `dsa add` to free VRAM |
| `whisper model 'small' not found` | put `ggml-small.bin` in `ai.whisper_model_dir` |
| the draft keeps failing review | the checklist header names the problem; an emptied `problem.yaml` aborts, and `dsa add --resume NAME` continues from the saved inbox |
