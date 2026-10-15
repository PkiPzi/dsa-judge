# Wrong: resets to 0 and starts best at 0, so all-negative arrays return 0.
class Solution:
    def maxSubarraySum(self, nums: List[int]) -> int:
        best = cur = 0
        for x in nums:
            cur = max(0, cur + x)
            best = max(best, cur)
        return best
