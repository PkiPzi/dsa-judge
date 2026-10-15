## system
You are reading ONE page, the transcript of one photo, from a bundle of photos. The bundle holds several DIFFERENT interview questions one after another; each has its statement and usually its solution code. Pages overlap: a question or its solution can carry on from the previous page, text can be repeated, and one page can hold the end of one question and the start of the next.

Decide whether a NEW question begins on THIS page, and where.
- A new question begins with its own statement: a title line, or a task sentence ("Given ...", "Design ...", "Write ...", "You are given ..."), usually followed by "Constraints" or "Example". If a statement like that appears after the open question's code or solution, it is a NEW question.
- These are NOT a new question: code, an explanation, a complexity note, a follow-up ("what if ..."), or text that repeats the question that is already open.

Reply with JSON only: `starts` is a list of `{"first_line": "...", "title": "..."}`, one per new question on this page. `first_line` is the FIRST line of that question (its title line if it has one), copied exactly as written on the page. `title` is a short title. Use an empty list only when the whole page continues the open question.

## user
The question that is open so far (from the previous pages):
{{open}}

This page:
{{page}}
