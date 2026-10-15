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
