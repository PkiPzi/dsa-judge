## system
You write an answer checker in Python for a problem that has several valid answers.
- Reply with one fenced ```python block and nothing else.
- Define `def check(args, expected, got)`: `args` is the list of input arguments, `expected` is a reference answer, `got` is the contestant's answer. Return `(True, "")` if `got` is a valid answer for `args`, else `(False, "short reason")`. Check validity against the problem statement; do not just compare with `expected`.
- Plain Python, standard library only. Never raise: malformed `got` (wrong type, wrong length) is simply `(False, ...)`.

## user
Statement:

{{statement}}

Method signature: {{signature}}
Examples (arguments, then one valid answer):
{{samples}}
{{feedback}}
