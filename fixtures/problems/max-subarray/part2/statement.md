# Follow-up: delete at most one element

Same input as before. This time, after choosing a contiguous block you may remove **at most one** element from it. The block must still contain at least one element after the removal. Return the largest sum you can end up with.

## Examples

**Example 1**
Input: `nums = [2, -5, 3, -1, 4]`
Output: `8`
Take the whole array and remove `-5`: 2 + 3 - 1 + 4 = 8.

**Example 2**
Input: `nums = [-3, -1, -4]`
Output: `-1`
You can't remove the only element of a one-element block, so the best is `[-1]`.

**Example 3**
Input: `nums = [1, 2, 3]`
Output: `6`
Removing nothing is allowed.

## Constraints
- 1 <= nums.length <= 10^5
- -10^4 <= nums[i] <= 10^4
