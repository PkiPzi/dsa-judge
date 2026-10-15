# Correct answer, but first builds a ~2.4 GB list (limit is 256 MB).
class Solution:
    def maxSubarraySum(self, nums: List[int]) -> int:
        waste = [1] * 300_000_000
        best, cur = nums[0], waste[len(nums)] - 1
        for x in nums:
            cur = max(x, cur + x)
            best = max(best, cur)
        return best
