// Correct answer, but first allocates ~1.2 GB (limit is 256 MB).
class Solution {
    public int maxSubarraySum(int[] nums) {
        int[] waste = new int[300_000_000];
        java.util.Arrays.fill(waste, 1);
        int best = nums[0], cur = waste[nums.length] - 1;
        for (int x : nums) {
            cur = Math.max(x, cur + x);
            best = Math.max(best, cur);
        }
        return best;
    }
}
