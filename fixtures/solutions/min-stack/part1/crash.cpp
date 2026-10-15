class MinStack {
    vector<int> st;

public:
    MinStack() {}
    void push(int val) { st.push_back(val); }
    void pop() { st.pop_back(); }
    int top() { throw runtime_error("top() is broken"); }
    int getMin() { return *min_element(st.begin(), st.end()); }
};
