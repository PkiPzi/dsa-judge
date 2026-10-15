class Solution {
    public int maxSumWithOneDeletion(int[] nums) {
        int keep = nums[0], removed = Integer.MIN_VALUE / 2, best = nums[0];
        for (int i = 1; i < nums.length; i++) {
            int x = nums[i];
            int nextRemoved = Math.max(removed + x, keep);
            keep = Math.max(keep + x, x);
            removed = nextRemoved;
            best = Math.max(best, Math.max(keep, removed));
        }
        return best;
    }
}
