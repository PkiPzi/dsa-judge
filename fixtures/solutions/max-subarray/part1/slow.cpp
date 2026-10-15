// O(n^2): every start, every end.
class Solution {
public:
    int maxSubarraySum(vector<int>& nums) {
        int n = nums.size();
        long long best = LLONG_MIN;
        for (int i = 0; i < n; i++) {
            long long s = 0;
            for (int j = i; j < n; j++) {
                s += nums[j];
                if (s > best) best = s;
            }
        }
        return (int)best;
    }
};
