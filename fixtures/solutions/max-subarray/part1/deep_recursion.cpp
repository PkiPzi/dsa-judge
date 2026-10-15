// Correct, but recursive to depth n with a padded frame: about 50 MB of stack at n = 1e5,
// far past Linux's default 8 MB. Passes only because the judge raises RLIMIT_STACK.
class Solution {
    // best sum of a block ending at i
    long long endingAt(const vector<int>& nums, int i, long long& best) {
        volatile char pad[400];
        pad[0] = (char)i;
        if (i == 0) {
            best = nums[0];
            return nums[0] + pad[0] * 0;
        }
        long long prev = endingAt(nums, i - 1, best);
        long long here = max<long long>(nums[i], prev + nums[i]) + pad[0] * 0;
        best = max(best, here);
        return here;
    }

public:
    int maxSubarraySum(vector<int>& nums) {
        long long best = 0;
        endingAt(nums, (int)nums.size() - 1, best);
        return (int)best;
    }
};
