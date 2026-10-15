// Reference for part 1 (Kadane). LeetCode-style: the driver supplies headers.
class Solution {
public:
    int maxSubarraySum(vector<int>& nums) {
        long long best = LLONG_MIN, cur = 0;
        for (int x : nums) {
            cur = max<long long>(x, cur + x);
            best = max(best, cur);
        }
        return (int)best;
    }
};
