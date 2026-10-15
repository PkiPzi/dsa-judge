# Kadane, O(n).
class Solution:
    def maxSubarraySum(self, nums: List[int]) -> int:
        best, cur = nums[0], 0
        for x in nums:
            cur = max(x, cur + x)
            best = max(best, cur)
        return best
