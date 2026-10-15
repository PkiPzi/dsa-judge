// Reference for part 2, written "by a friend" in Java to exercise multi-language refs.
class Solution {
    public int maxSumWithOneDeletion(int[] nums) {
        int keep = nums[0];                 // best block ending here, nothing removed
        int removed = Integer.MIN_VALUE / 2; // best block ending here, one element removed
        int best = nums[0];
        for (int i = 1; i < nums.length; i++) {
            int x = nums[i];
            int nextRemoved = Math.max(removed + x, keep); // extend, or remove x itself
            int nextKeep = Math.max(keep + x, x);
            keep = nextKeep;
            removed = nextRemoved;
            best = Math.max(best, Math.max(keep, removed));
        }
        return best;
    }
}
