## system
You are reading ONE page, the transcript of one photo, from a bundle of photos. The bundle holds several DIFFERENT interview questions one after another; each has its statement and usually its solution code. Pages overlap: a question or its solution can carry on from the previous page, text can be repeated, and one page can hold the end of one question and the start of the next.

Decide where on THIS page a NEW question begins.
- A new question begins at the line that starts a different problem statement: a title, a problem number, or the first sentence of a new task ("Given ...", "Design ...", "Write ...", "You are given ...").
- These are NOT a new question: the solution, code, an explanation, a complexity note, examples, constraints, a follow-up ("what if ..."), or text that repeats the question that is already open.
- When unsure, say the page continues the open question: splitting one question in two is worse than missing a boundary.

Reply with JSON only: `starts` is a list of `{"line": N, "title": "..."}`, one per new question on this page. `line` is the number in [brackets] of the first line of that question; `title` is a short title. Use an empty list when the whole page continues the open question.

## user
The question that is open so far (from the previous pages):
{{open}}

This page, with line numbers:
{{page}}
