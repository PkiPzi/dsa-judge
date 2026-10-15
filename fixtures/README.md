# Fixtures (public only)

Everything here is a well-known public problem with the statement rewritten in our own words. Real interview questions live in `$DSA_HOME` on the user's machine and must never be copied here.

- `problems/max-subarray/`: a 3-part problem used as the end-to-end fixture.
  - Part 1: C++ ref + Python brute.
  - Part 2: Java ref (simulates a friend's solution) + Python brute.
  - Part 3: no ref, only an unverified brute (exercises `brute-only` oracle mode).
  - The refs and brutes were cross-checked on 3,000 random small arrays plus max-size cases before this fixture was committed.
- `solutions/max-subarray/part1/`: `correct`, `wrong`, `slow` (O(n²)), `crash`, `deep_recursion` and `memory_hog` variants in `.cpp`, `.java` and `.py`. `expected.yaml` lists each variant's `dsa submit` verdict once the large cases exist (`python tests/make_large_cases.py PROBLEM_DIR` writes `partN/tests/manual/large-*.in.json`). `part2/` holds correct solutions for the unlock flow.
- `dsa build max-subarray` builds all three parts: parts 1-2 in `ref` mode (stressed against the brute on 300+ seeds) and part 3 in `brute-only` mode. The build writes `partN/tests/` into the bank copy (`$DSA_HOME/problems/max-subarray`), never into `fixtures/`.
- Hand-written test inputs go in `problems/<slug>/partN/tests/manual/*.in.json` (one JSON value per line per parameter). Their expected outputs are computed by running the refs, never written by hand.
- `ai/` (added in Phase 3): recorded responses for `FakeLLM` and `FakeTranscriber`. Intake images are rendered from text at test time, not stored as real photos.
- `ai/max-product.json`: recorded model responses for `FakeLLM` / `FakeTranscriber`, for a synthetic 2-part problem ("maximum product of two / three numbers", written for this repo). The first `fill_constraints` answer for part 1 is deliberately wrong (a bound that contradicts a sample) so the verify-and-retry path is exercised. The statement "photo" is rendered with Pillow at test time (`dsajudge.ai.synth`, including a superscript `10^5`), and the voice note is a generated tone whose transcript is recorded here, in Hinglish. Real models are only exercised by `pytest -m local`.
