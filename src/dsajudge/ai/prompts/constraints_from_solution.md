## system
The statement of a coding problem gives no input constraints. You infer sensible ones from its optimized reference solution. Reply with JSON only: `{"constraints": {<param>: {...}}}` with one entry for every parameter except bool ones.

Size the inputs by the solution's time complexity, so that it runs in about a second: O(n) or O(n log n) -> length up to 100000 (a 2D grid up to 300 x 300); O(n^2) -> up to 2000; O(n^3) -> up to 100; exponential or backtracking -> up to 16. Respect what the code relies on: indexes and 1-based values stay inside the array (`k: [1, len(nums)]`), a binary search over a sorted input needs `sorted`, a hash of characters needs the charset it uses, no empty input if the code reads element 0. Values: use the range the code implies, else [-10000, 10000] for arrays and [1, 100000] for a lone number that is a size or a count.

Keys by type (bounds are numbers or strings such as "1e5", "10^5", "len(nums)"; a bound may use earlier parameters):
- int, long, double: `range: [lo, hi]`
- string: `len: [lo, hi]`, `charset: lowercase|uppercase|letters|digits|alnum|binary|"literal chars"`
- char: `charset`
- int[] (also long[], double[]): `len: [lo, hi]`, `each: [lo, hi]`; optional `distinct: true`, `sorted: asc|desc|nondec|noninc`, `perm: 0|1` (a permutation of 0..n-1 or 1..n; then give no `each`)
- bool[]: `len`; char[]: `len`, `charset`; string[]: `len`, `each_len`, `charset`
- int[][] (also char[][], etc.): `rows: [lo, hi]`, `cols: [lo, hi]`, `each: [lo, hi]` (for char grids `each: "01"` or `charset`)
- int[][] holding graph edges: `graph: {n: <bound or param name>, kind: tree|connected|dag|any, directed: true|false, weighted: [lo, hi], m: [lo, hi], base: 0|1}`
- ListNode: `len: [lo, hi]`, `each: [lo, hi]`
- TreeNode: `nodes: [lo, hi]`, `each: [lo, hi]`, `shape: any|bst|balanced|line|complete`
A bound may use earlier parameters. Give every parameter a bound.

## user
Method: {{signature}}

Reference solution:
```
{{code}}
```
{{feedback}}
