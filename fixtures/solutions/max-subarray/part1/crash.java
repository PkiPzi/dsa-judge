// Reads one past the end on arrays longer than 3: ArrayIndexOutOfBoundsException.
class Solution {
    public int maxSubarraySum(int[] nums) {
        int best = nums[0], cur = 0;
        if (nums.length > 3) best += nums[nums.length];
        for (int x : nums) {
            cur = Math.max(x, cur + x);
            best = Math.max(best, cur);
        }
        return best;
    }
}
