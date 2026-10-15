# O(n^2): every start, every end.
class Solution:
    def maxSubarraySum(self, nums: List[int]) -> int:
        best = nums[0]
        for i in range(len(nums)):
            s = 0
            for j in range(i, len(nums)):
                s += nums[j]
                if s > best:
                    best = s
        return best
