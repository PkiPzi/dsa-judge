// Wrong: resets to 0 and starts best at 0, so all-negative arrays return 0.
class Solution {
    public int maxSubarraySum(int[] nums) {
        int best = 0, cur = 0;
        for (int x : nums) {
            cur = Math.max(0, cur + x);
            best = Math.max(best, cur);
        }
        return best;
    }
}
