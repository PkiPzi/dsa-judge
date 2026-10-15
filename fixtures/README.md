# Fixtures (public only)

Everything here is a well-known public problem with the statement rewritten in our own words. Real interview questions live in `$DSA_HOME` on the user's machine and must never be copied here.

- `problems/max-subarray/`: a 3-part problem used as the end-to-end fixture.
  - Part 1: C++ ref + Python brute.
  - Part 2: Java ref (simulates a friend's solution) + Python brute.
  - Part 3: no ref, only an unverified brute (exercises `brute-only` oracle mode).
  - The refs and brutes were cross-checked on 3,000 random small arrays plus max-size cases before this fixture was committed.
- `solutions/` (added in Phase 1): correct / wrong / slow / crash / deep-recursion / memory-hog variants per language.
- `ai/` (added in Phase 3): recorded responses for `FakeLLM` and `FakeTranscriber`. Intake images are rendered from text at test time, not stored as real photos.
