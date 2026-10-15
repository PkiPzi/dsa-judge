// Wrong: resets to 0 and starts best at 0, so all-negative arrays return 0.
class Solution {
public:
    int maxSubarraySum(vector<int>& nums) {
        int best = 0, cur = 0;
        for (int x : nums) {
            cur = max(0, cur + x);
            best = max(best, cur);
        }
        return best;
    }
};
