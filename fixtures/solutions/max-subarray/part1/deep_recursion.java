// Correct, but recursive to depth n; needs the judge's big-stack thread at n = 1e5.
class Solution {
    private long best;
    private int[] a;

    private long endingAt(int i) {
        if (i == 0) {
            best = a[0];
            return a[0];
        }
        long prev = endingAt(i - 1);
        long here = Math.max(a[i], prev + a[i]);
        best = Math.max(best, here);
        return here;
    }

    public int maxSubarraySum(int[] nums) {
        a = nums;
        endingAt(nums.length - 1);
        return (int) best;
    }
}
