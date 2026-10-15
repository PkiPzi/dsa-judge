// Kadane, O(n).
class Solution {
    public int maxSubarraySum(int[] nums) {
        int best = nums[0], cur = 0;
        for (int x : nums) {
            cur = Math.max(x, cur + x);
            best = Math.max(best, cur);
        }
        return best;
    }
}
