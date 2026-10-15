# Correct, but recursive to depth n; needs the raised recursion limit and big thread stack.
class Solution:
    def maxSubarraySum(self, nums: List[int]) -> int:
        best = [nums[0]]

        def ending_at(i: int) -> int:
            if i == 0:
                return nums[0]
            here = max(nums[i], ending_at(i - 1) + nums[i])
            best[0] = max(best[0], here)
            return here

        ending_at(len(nums) - 1)
        return best[0]
