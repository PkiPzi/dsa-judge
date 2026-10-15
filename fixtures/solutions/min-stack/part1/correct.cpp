class MinStack {
    vector<int> st, mn;

public:
    MinStack() {}

    void push(int val) {
        st.push_back(val);
        mn.push_back(mn.empty() ? val : min(mn.back(), val));
    }

    void pop() {
        st.pop_back();
        mn.pop_back();
    }

    int top() { return st.back(); }

    int getMin() { return mn.back(); }
};
