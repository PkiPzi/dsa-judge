// Correct answer, but first fills ~1.2 GB (limit is 256 MB).
class Solution {
public:
    int maxSubarraySum(vector<int>& nums) {
        vector<int> waste(300'000'000, 1);
        int best = nums[0], cur = waste[nums.size() % waste.size()] - 1;
        for (int x : nums) {
            cur = max(x, cur + x);
            best = max(best, cur);
        }
        return best;
    }
};
