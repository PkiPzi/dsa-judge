# Maximum subarray sum

You are given an integer array `nums`. Pick a contiguous, non-empty block of elements and return the largest sum such a block can have.

## Examples

**Example 1**
Input: `nums = [3, -4, 5, -1, 2, -6, 1]`
Output: `6`
The block `[5, -1, 2]` sums to 6.

**Example 2**
Input: `nums = [-7, -3, -9]`
Output: `-3`
Every element is negative, so the best block is the single largest element.

**Example 3**
Input: `nums = [4]`
Output: `4`

## Constraints
- 1 <= nums.length <= 10^5
- -10^4 <= nums[i] <= 10^4
