# Follow-up: circular array

Now treat `nums` as circular: the element after the last one is the first one. A block may wrap around the end, but it may use each position at most once. Return the largest sum of a non-empty block.

## Examples

**Example 1**
Input: `nums = [5, -8, 6]`
Output: `11`
The block wraps around: 6, then 5.

**Example 2**
Input: `nums = [-4, -2, -6]`
Output: `-2`

**Example 3**
Input: `nums = [2, -1, 3, -10, 4]`
Output: `8`
The block 4, 2, -1, 3 wraps around the end.

## Constraints
- 1 <= nums.length <= 10^5
- -10^4 <= nums[i] <= 10^4
