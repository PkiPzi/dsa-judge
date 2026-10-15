class MinStack {
    private final ArrayList<Integer> st = new ArrayList<>();
    private int best = Integer.MAX_VALUE;

    public MinStack() {}
    public void push(int val) { st.add(val); best = Math.min(best, val); }
    public void pop() { st.remove(st.size() - 1); }
    public int top() { return st.get(st.size() - 1); }
    public int getMin() { return best; }
}
