class MinStack {
    private final ArrayDeque<Integer> st = new ArrayDeque<>();

    public MinStack() {}
    public void push(int val) { st.push(val); }
    public void pop() { st.pop(); }
    public int top() { return st.peek(); }
    public int getMin() {
        ArrayDeque<Integer> tmp = new ArrayDeque<>();
        int best = st.peek();
        while (!st.isEmpty()) {
            int x = st.pop();
            best = Math.min(best, x);
            tmp.push(x);
        }
        while (!tmp.isEmpty()) st.push(tmp.pop());
        return best;
    }
}
