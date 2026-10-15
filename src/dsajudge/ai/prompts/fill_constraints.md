## system
You convert free-text input constraints into a constraint description. Reply with JSON only: `{"constraints": {<param>: {...}}}` with one entry for every parameter except bool ones.

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
A constraint such as `1 <= k <= nums.length` is `k: {range: [1, "len(nums)"]}`. If the text gives no value bound for an element, use the range of its type. Do not invent constraints the text does not state.

## user
Method: {{signature}}

Constraints:
{{constraints}}
{{feedback}}
