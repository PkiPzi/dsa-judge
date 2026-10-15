class MinStack {
    vector<int> st;
    int best = INT_MAX;

public:
    MinStack() {}
    void push(int val) {
        st.push_back(val);
        best = min(best, val);
    }
    void pop() { st.pop_back(); }
    int top() { return st.back(); }
    int getMin() { return best; }
};
