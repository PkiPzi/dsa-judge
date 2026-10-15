class MinStack {
    private final ArrayList<Integer> st = new ArrayList<>();

    public MinStack() {}
    public void push(int val) { st.add(val); }
    public void pop() { st.remove(st.size() - 1); }
    public int top() { throw new IllegalStateException("top() is broken"); }
    public int getMin() { return Collections.min(st); }
}
