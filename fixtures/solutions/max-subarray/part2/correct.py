class Solution:
    def maxSumWithOneDeletion(self, nums: List[int]) -> int:
        keep, removed, best = nums[0], float("-inf"), nums[0]
        for x in nums[1:]:
            keep, removed = max(keep + x, x), max(removed + x, keep)
            best = max(best, keep, removed)
        return int(best)
