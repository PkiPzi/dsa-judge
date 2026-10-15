from typing import List


class Solution:
    # Stands in for an LLM-written brute (origin: llm, verified: false).
    # O(n^2): every start position, every length up to n, walking around the circle.
    def maxCircularSubarraySum(self, nums: List[int]) -> int:
        n = len(nums)
        best = nums[0]
        for start in range(n):
            s = 0
            for length in range(n):
                s += nums[(start + length) % n]
                best = max(best, s)
        return best
