// O(n^2): every start, every end.
class Solution {
    public int maxSubarraySum(int[] nums) {
        long best = Long.MIN_VALUE;
        for (int i = 0; i < nums.length; i++) {
            long s = 0;
            for (int j = i; j < nums.length; j++) {
                s += nums[j];
                if (s > best) best = s;
            }
        }
        return (int) best;
    }
}
