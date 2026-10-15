# LRU cache

Design a fixed-size cache that throws away the entry that was used least recently.

Implement the class `LRUCache`:

- `LRUCache(capacity)` creates an empty cache that holds at most `capacity` entries.
- `get(key)` returns the value stored under `key`, or `-1` if there is none. A successful `get`
  counts as a use of that key.
- `put(key, value)` stores `value` under `key` (replacing an old value, which also counts as a
  use). If that makes the cache hold more than `capacity` entries, the entry whose last use is
  the oldest is removed.

Both methods must run in O(1) time on average.

## Constraints

- 1 <= capacity <= 3000
- 0 <= key <= 10^4, 0 <= value <= 10^5
- At most 2 * 10^5 calls.

## Example

Capacity 2: `put(1,10)`, `put(2,20)`, `get(1)` is 10, `put(3,30)` evicts key 2, so `get(2)` is -1;
`put(4,40)` evicts key 1, so `get(1)` is -1, while `get(3)` is 30 and `get(4)` is 40.
