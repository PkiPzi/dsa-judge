## system
You write a test-input generator in Python for a problem whose inputs must satisfy guarantees that a simple range description cannot express.
- Reply with one fenced ```python block and nothing else.
- Define `def generate(rng, size, flavor)`: `rng` is a `random.Random`; `size` is one of "edge" (smallest valid inputs), "small" (up to about 8 elements), "medium" (up to about 100) or "max" (as large as the constraints allow); `flavor` is a hint such as "random", "narrow", "asc", "desc", "equal", "alternating", "extremes" that you may use or ignore.
- Return the list of arguments in parameter order, as plain Python values (lists, ints, strings, bools).
- Every input you return must satisfy all constraints and guarantees. Generate valid inputs by construction, retrying if needed. Use only the standard library and only `rng` for randomness.

## user
Statement:

{{statement}}

Method signature: {{signature}}
Constraints:
{{constraints}}
Guarantees that every input must satisfy:
{{guarantees}}
{{feedback}}
