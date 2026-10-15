from typing import List


class Solution:
    # O(n^2): try every block.
    def maxSubarraySum(self, nums: List[int]) -> int:
        best = nums[0]
        for i in range(len(nums)):
            s = 0
            for j in range(i, len(nums)):
                s += nums[j]
                best = max(best, s)
        return best
