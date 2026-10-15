class MinStack {
    stack<int> st;

public:
    MinStack() {}
    void push(int val) { st.push(val); }
    void pop() { st.pop(); }
    int top() { return st.top(); }
    int getMin() {
        stack<int> tmp;
        int best = st.top();
        while (!st.empty()) {
            best = min(best, st.top());
            tmp.push(st.top());
            st.pop();
        }
        while (!tmp.empty()) {
            st.push(tmp.top());
            tmp.pop();
        }
        return best;
    }
};
