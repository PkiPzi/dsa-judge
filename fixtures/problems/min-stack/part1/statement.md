# Min stack

Build a stack of integers that can report its smallest element instantly.

Implement the class `MinStack`:

- `MinStack()` creates an empty stack.
- `push(val)` puts `val` on top.
- `pop()` removes the top element.
- `top()` returns the top element.
- `getMin()` returns the smallest element currently in the stack.

`pop`, `top` and `getMin` are only called on a non-empty stack. Every method must run in
O(1) time.

The judge calls your class with a list of operation names and a list of argument lists, and
compares the list of results (`null` for the constructor and for methods that return nothing).

## Constraints

- -10^4 <= val <= 10^4
- At most 10^5 calls.

## Example

Operations `["MinStack","push","push","push","getMin","pop","top","getMin"]` with arguments
`[[],[-2],[0],[-3],[],[],[],[]]` give `[null,null,null,null,-3,null,0,-2]`.
