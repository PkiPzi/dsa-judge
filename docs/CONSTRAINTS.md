# Constraint DSL and test generation

Reference for the `constraints` block in `problem.yaml` (PLAN.md §6.1) and for how `dsa build`
turns it into hidden tests. Everything here is deterministic and involves no AI.

## Bounds

Every bound is parsed by the DSL, not by YAML (YAML reads `1e5` as a string). Write ints,
floats or strings:

| Form | Meaning |
|---|---|
| `1e5`, `2e5`, `1e18` | exact integers when integral |
| `10^5`, `2*10^5`, `10^9 + 7`, `2^31 - 1` | `^` is a power; `-10^4` is `-(10^4)` |
| `len(nums)`, `len(s) // 2`, `n - 1` | refer to **earlier** params, in signature order |
| `min(a, b)`, `max(len(x), 1)`, `sum(a)`, `abs(k)` | `min`/`max` also take one array |

A bound that refers to a later param (or to itself) is an error that names the param.

## Keys per type

| Type | Keys |
|---|---|
| `int`, `long` | `range: [lo, hi]` |
| `double` | `range`, `decimals` (default 2) |
| `bool` | none |
| `char` | `charset` |
| `string` | `len`, `charset` |
| `int[]`, `long[]`, `double[]` | `len`, `each`; `distinct`, `sorted: asc\|desc\|nondec\|noninc`, `perm: 0\|1` (int/long) |
| `bool[]` | `len` |
| `char[]` | `len`, `charset` |
| `string[]` | `len`, `each_len`, `charset` |
| `T[][]` | `rows`, `cols`, `each` (numbers) or `charset`/`each: "01"` (chars); `ragged: true` |
| `int[][]` as a graph | `graph: {n, kind, directed, weighted, m, base}` |
| `ListNode` | `len` (or `nodes`), `each`; `distinct`, `sorted` |
| `TreeNode` | `nodes`, `each`; `shape: any\|bst\|balanced\|line\|complete`, `distinct` |

`charset` is `lowercase`, `uppercase`, `letters`, `digits`, `alnum`, `binary`, or a literal string
of allowed characters (`"abc#"`).

A param used as a dimension by a later param (`n` in `nums: {len: [n, n]}`) is treated as a
**size**: small tests keep it small and max tests set it to its upper bound.

```yaml
constraints:
  n:     {range: [1, 1e5]}
  nums:  {len: [n, n], each: [-1e9, 1e9]}
  k:     {range: [1, len(nums)]}
  s:     {len: [1, 2e5], charset: lowercase}
  grid:  {rows: [1, 300], cols: [1, 300], each: "01"}
  root:  {nodes: [0, 1e4], each: [-1000, 1000], shape: bst}
  edges: {graph: {n: n, kind: connected, weighted: [1, 1e9], base: 1}}
```

Graphs are simple (no self loops or parallel edges). `kind` is `tree`, `connected`, `dag` (always
directed) or `any`; `m: [lo, hi]` bounds the edge count (default: `n-1` for trees, up to `2n`
otherwise); `base` is the first node label (0 or 1).

## The test plan

Per part (counts in `$DSA_HOME/config.toml` under `[plan]`):

| Group | Count | What |
|---|---|---|
| `edge` | ~18 tried, duplicates dropped | minimum sizes, all-min / all-max / all-equal / zeros / extremes |
| `small_random` | 40 | tiny arrays (default length ≤ 8): this is what the brute checks |
| `medium_random` | 10 | up to 100 elements (2-D: 20 per side) |
| `max` | 6 | maximum size: random, sorted, reverse, all-equal, alternating signs, extreme values |
| stress | 300 extra | small seeds for ref-vs-brute only; never stored |

Seeds are `H(slug, part, group, index)`. The RNG is built on `getrandbits` only, so the same seed
gives the same input on any Python version.

**Flavors** shape the values, and for structured types the structure:

| Flavor | Arrays / strings | Trees | Graphs |
|---|---|---|---|
| `random` | uniform | random shape | random |
| `narrow` | few distinct values (forces duplicates) | same | same |
| `asc` / `desc` | sorted | right / left line | path |
| `equal` | all equal | same | star |
| `alternating` | alternating signs or extremes | zigzag line | caterpillar (trees, connected graphs) |
| `extremes` | only the bounds | balanced | binary tree (trees, connected) or max edges |

A flavor that contradicts a constraint (`equal` with `distinct`) falls back to `random`.

## Guarantees and custom generators

`guarantees` are promises the DSL cannot express ("exactly one answer exists"). A part with
guarantees must supply `partN/gen.py`:

```python
def generate(rng: random.Random, size: str, flavor: str) -> list:  # size: edge|small|medium|max
    ...
```

and may supply `partN/validate.py` (`def validate(args) -> str | None`, an error message or
`None`). Both run in a separate isolated interpreter. The DSL validator still checks every input,
and `gen.py` must pass it on 200 extra inputs.

## What `dsa build` checks, in order

1. Every sample satisfies the constraints. A violation stops the build and names the
   `problem.yaml` line (usually an OCR slip such as `10^5` read as `105`).
2. Every generated input passes the validator.
3. Every ref compiles and runs on every test, matches the samples, and agrees with the other refs.
4. If a brute exists: ref vs brute on `edge`, `small_random`, the samples and 300 extra seeds.
   A mismatch is shrunk to the smallest counterexample and shown.
5. The time limit is `clamp(3 × slowest C++ ref test, 1 s, 5 s)` unless `limits.time_ms` is set.
6. **Weak-test check**: if the brute passes every `max` test within the limit, the build warns
   that the tests don't separate complexities. A limit too high for the check to settle it also
   warns.

Then the tests are written: small ones to `partN/tests/small/` (input and answer), large ones
only recorded in `partN/tests/manifest.json` (seed, input hash, answer hash) and regenerated
into the cache when you submit. Anything that no longer matches its hash is a judge error.

## Oracle modes

| Mode | When | Verdicts |
|---|---|---|
| `ref` | a ref exists | full verdicts on every test |
| `brute-only` | no ref, a brute exists | `edge`/`small`/`medium` tests judged against the brute (`WA?`); `max` tests are run-only (TLE/MLE/RE only) |
| `promoted` | you ran `dsa promote` | as `ref` |

- `dsa promote`: your AC solution becomes the ref. It must have been accepted by `dsa submit` in
  exactly its current form, and must agree with the brute on the stress inputs, or nothing changes.
- `dsa dispute TEST_ID`: you believe the brute is wrong on one test. The test becomes run-only and
  the part shows `needs-review` in `dsa show`. `--clear` withdraws it. Samples can't be disputed.

## stdio mode

`mode: stdio` parts are full programs. The part still needs a `signature` (its params and
`returns` type describe the generated input and expected output; the method name is ignored) and
an `input_format`:

```yaml
mode: stdio
input_format: |
  {len(nums)}
  {nums}
```

`{name}` renders a param: scalars as decimal (`bool` is `1`/`0`), `T[]` space-separated
(`{nums:lines}` one per line, `{nums:concat}` no separator), `T[][]` one row per line. `{expr}`
works too (`{n - 1}`); `{{` and `}}` are literal braces. `output_format` (default `{result}`)
renders the expected result. Output is compared token by token (whitespace-insensitive); with
`checker: float:1e-6` numeric tokens match within tolerance, and `unordered` ignores token order.

Java solutions are `public class Solution` with `main`, as on HackerRank. Time is wall clock minus
the language's fixed start-up cost (JVM boot, interpreter start).

### Exporting stdio parts

`dsa export SLUG [--part N] [--format hackerrank|hackerearth|both] [--count N|all|auto]` writes a
test-case zip into `$DSA_HOME/export/SLUG/` plus a `.notes.txt` listing each test, its score and
anything skipped. Formats follow the vendors' help centres (checked 2026-10-15):

| | HackerRank | HackerEarth |
|---|---|---|
| Files | `input00.txt` / `output00.txt` pairs | `in00.txt` / `out00.txt` pairs |
| Zip | flat, no sub-directories | flat, "not put in a folder" |
| Input rules | none documented | no blank lines, no leading whitespace on the first line, no trailing whitespace |
| Scores | 10 each on upload | you assign them; the notes suggest a split that totals 100 |
| Default count | 15 (their 3-15 guidance) | 10 (their recommendation) |

Sources: [HackerRank: upload all test cases at once](https://support.hackerrank.com/articles/5773853281-how-do-i-upload-all-test-cases-at-once),
[HackerEarth: creating custom programming questions](https://help.hackerearth.com/hc/en-us/articles/360005444233-creating-custom-programming-questions),
[HackerEarth: test case format](https://help.hackerearth.com/hc/en-us/articles/9810665517081-test-case-format).

The samples always come first. With fewer tests than the suite holds, the rest are spread over
`edge`, `small_random`, `medium_random` and `max` (weighted toward `max`). Tests whose input a
platform would reject (an empty array rendering a blank line, for HackerEarth) are skipped and
listed. Answers come only from running a ref (or the flagged brute); tests with no answer
(`brute-only` max tests) are never exported. Zips are byte-for-byte reproducible.

## Design problems (`mode: ops`)

For "implement an LRU cache" style questions: a class with a constructor and methods, judged on a
sequence of calls in LeetCode's wire format. A test input is two lines, the operation names (the
class name first) and one argument list per operation; the expected output has one result per
operation, `null` for the constructor and for `void` methods. `dsa run --case` takes the same two
lines.

```yaml
mode: ops
design:
  class: MinStack
  counters: {size: 0}                   # tracked by the generator only (see below)
  methods:
    - {name: push, params: [{name: val, type: int}], effects: {size: 1}}
    - {name: pop, requires: {size: 1}, effects: {size: -1}}
    - {name: top, returns: int, requires: {size: 1}}
    - {name: getMin, returns: int, requires: {size: 1}}
constraints:
  calls: {len: [1, 1e5], mix: {push: 3, pop: 1, top: 1, getMin: 6}}
  push: {val: {range: [-1e4, 1e4]}}
samples:
  - args: [["MinStack","push","push","getMin"], [[],[5],[2],[]]]
    expected: [null, null, null, 2]
```

* `design.constructor` lists the constructor's params (omit if none); method params and `returns`
  use the usual types (`returns` defaults to `void`).
* `constraints.calls.len` is the number of method calls after the constructor; `mix` gives each
  method's relative frequency (default 1; omitted methods in a given `mix` are never called).
* `constraints.ctor` has one spec per constructor param; each method has an entry with one spec
  per param (the key names are the method names, so `calls` and `ctor` are reserved). Bounds may
  use constructor params and earlier params of the same call: `key: {range: [0, "2 * capacity"]}`
  makes an LRU cache's keys collide often enough to evict. Constructor values scale with the test
  size like array lengths do, so small tests have small capacities.
* **Counters** keep sequences legal without a custom generator. A method with
  `requires: {size: 1}` is only called while `size >= 1`; `effects` change the counters after the
  call. A counter starts at an int or an expression over constructor params (`size: "len(xs)"`).
  For anything else (a promise like "keys are never reused"), use `guarantees` and a `gen.py`
  returning `[ops, args]`.
* Flavors shape the sequence: `asc` makes methods that raise a counter 4x as likely (the
  structure gets big, which is what exposes an O(n) `getMin`), `desc` builds for the first half
  and then drains. A "small" test allows 4x the usual small length, since stateful bugs need a
  few dozen calls.
* Refs and brutes are classes in any language and must agree on every test, like any other part;
  the C++ stub is `class MinStack { public: ... };`, Java `class MinStack`, Python
  `class MinStack:`. Time is the sum of the time spent inside the constructor and method calls.
* A wrong answer names the first call that differs: `call #7 getMin() returned -3, expected -2`.
  Stress mismatches are shrunk by deleting calls, so you see a handful of them.
* Checkers: `exact`, `float:EPS` or `custom`. `unordered` does not apply to a result list.
* Not supported yet: AI-assisted intake of design problems. The extraction pass only knows
  function and stdio signatures, so write the `design:` block by hand (or edit it in during the
  review) and put the statement, refs and brute in the problem directory; `dsa import DIR` adds it.
