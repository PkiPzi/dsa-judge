class MinStack {
    private final ArrayList<Integer> st = new ArrayList<>();
    private final ArrayList<Integer> mn = new ArrayList<>();

    public MinStack() {}

    public void push(int val) {
        st.add(val);
        mn.add(mn.isEmpty() ? val : Math.min(mn.get(mn.size() - 1), val));
    }

    public void pop() {
        st.remove(st.size() - 1);
        mn.remove(mn.size() - 1);
    }

    public int top() { return st.get(st.size() - 1); }

    public int getMin() { return mn.get(mn.size() - 1); }
}
