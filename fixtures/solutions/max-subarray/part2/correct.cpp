// Two-state DP: best block ending here with nothing removed / with one element removed.
class Solution {
public:
    int maxSumWithOneDeletion(vector<int>& nums) {
        int keep = nums[0], removed = INT_MIN / 2, best = nums[0];
        for (size_t i = 1; i < nums.size(); i++) {
            int x = nums[i];
            int nextRemoved = max(removed + x, keep);
            keep = max(keep + x, x);
            removed = nextRemoved;
            best = max({best, keep, removed});
        }
        return best;
    }
};
