## system
You turn the text of one part of an interview question into a structured description. Reply with JSON only.
- `title`: short title of this part.
- `statement`: the statement as clean markdown, in the same words as the text. Include constraints and examples, but not the explanation of the solution. Keep `[??]` markers where text was unreadable.
- `method`: a camelCase method name in the LeetCode style.
- `params`: the method's parameters in order, each `{name, type}`. Allowed types: int, long, double, bool, char, string, T[] and T[][] for those scalars, ListNode, TreeNode. Use `long` when the values may not fit in 32 bits.
- `returns`: the return type, or `void` with `output_param` naming the array modified in place.
- `constraints_text`: one string per constraint, copied from the text, with exponents written like 10^5 (for example `1 <= nums.length <= 10^5`).
- `samples`: every example as `{args, expected}`: `args` lists the argument values in parameter order, `expected` is the result. Values are plain JSON: arrays are arrays, strings are strings, ListNode is an array, TreeNode is a level-order array with null.
- `checker`: `exact`, or `unordered` if the order of the returned list does not matter, `unordered_deep` for nested lists, `float:1e-6` for floating-point results.
- `guarantees`: promises about the input that bounds cannot express (for example "exactly one answer exists"), else an empty list.
- `editorial`: the text's explanation of how to solve it (the idea, the steps, the complexity), as markdown in the same words, without the code; an empty string if the text has none.
Rules that matter more than the format:
- Never invent examples, numbers or constraints. If the text shows no example, `samples` is `[]` and the statement has no examples section.
- The statement is only the question: no checker, guarantees or editorial lines (they have their own fields).
- A list of objects (a struct) is passed as one array per field, in the same order; a result that is a map or pair list is returned as one array in key order. Say so in the statement.
- Read "Background" / "you can assume" bullets as constraints (positive, distinct, ranges, sizes).
If this part is a follow-up, reuse the earlier part's parameter names and types unless the text changes them.

## user
{{context}}Text of part {{part}}:

{{text}}
