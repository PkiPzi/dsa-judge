from typing import List


class Solution:
    # O(n^2): for every block, best of "remove nothing" and "remove its minimum".
    def maxSumWithOneDeletion(self, nums: List[int]) -> int:
        best = nums[0]
        for i in range(len(nums)):
            s = 0
            lo = nums[i]
            for j in range(i, len(nums)):
                s += nums[j]
                lo = min(lo, nums[j])
                best = max(best, s)
                if j > i:
                    best = max(best, s - lo)
        return best
