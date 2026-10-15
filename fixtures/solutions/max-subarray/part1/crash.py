# Reads one past the end on arrays longer than 3: IndexError.
class Solution:
    def maxSubarraySum(self, nums: List[int]) -> int:
        best, cur = nums[0], 0
        if len(nums) > 3:
            best += nums[len(nums)]
        for x in nums:
            cur = max(x, cur + x)
            best = max(best, cur)
        return best
