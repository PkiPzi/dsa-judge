// Crashes with SIGSEGV on arrays longer than 3 (a null write the optimizer can't drop).
class Solution {
public:
    int maxSubarraySum(vector<int>& nums) {
        if (nums.size() > 3) {
            volatile int* p = nullptr;
            *p = 42;
        }
        int best = nums[0], cur = 0;
        for (int x : nums) {
            cur = max(x, cur + x);
            best = max(best, cur);
        }
        return best;
    }
};
