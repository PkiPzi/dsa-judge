// dsa-judge: minimal JSON support for the Java driver. Hand-written, no dependencies.
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.function.Function;

final class DsaJson {
    private DsaJson() {}

    /** A JSON number kept as its token text, so longs past 2^53 stay exact. */
    static final class Num {
        final String text;
        Num(String text) { this.text = text; }
    }

    // ------------------------------------------------------------ parsing

    private static final class Parser {
        final String s;
        int i = 0;
        Parser(String s) { this.s = s; }

        void ws() {
            while (i < s.length()) {
                char c = s.charAt(i);
                if (c == ' ' || c == '\t' || c == '\r' || c == '\n') i++;
                else break;
            }
        }

        RuntimeException fail(String what) { return new IllegalArgumentException("bad JSON: " + what); }

        Object value() {
            ws();
            if (i >= s.length()) throw fail("unexpected end");
            char c = s.charAt(i);
            if (c == '[') {
                i++;
                ArrayList<Object> a = new ArrayList<>();
                ws();
                if (i < s.length() && s.charAt(i) == ']') { i++; return a; }
                while (true) {
                    a.add(value());
                    ws();
                    if (i < s.length() && s.charAt(i) == ',') { i++; continue; }
                    if (i < s.length() && s.charAt(i) == ']') { i++; return a; }
                    throw fail("expected , or ]");
                }
            }
            if (c == '"') return str();
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            if (c == '-' || (c >= '0' && c <= '9')) {
                int st = i++;
                while (i < s.length()) {
                    char d = s.charAt(i);
                    if ((d >= '0' && d <= '9') || d == '.' || d == 'e' || d == 'E' || d == '+' || d == '-') i++;
                    else break;
                }
                return new Num(s.substring(st, i));
            }
            throw fail("unexpected character");
        }

        String str() {
            i++;
            StringBuilder b = new StringBuilder();
            while (true) {
                if (i >= s.length()) throw fail("unterminated string");
                char c = s.charAt(i++);
                if (c == '"') return b.toString();
                if (c != '\\') { b.append(c); continue; }
                if (i >= s.length()) throw fail("unterminated escape");
                char x = s.charAt(i++);
                switch (x) {
                    case '"': b.append('"'); break;
                    case '\\': b.append('\\'); break;
                    case '/': b.append('/'); break;
                    case 'b': b.append('\b'); break;
                    case 'f': b.append('\f'); break;
                    case 'n': b.append('\n'); break;
                    case 'r': b.append('\r'); break;
                    case 't': b.append('\t'); break;
                    case 'u':
                        if (i + 4 > s.length()) throw fail("short \\u escape");
                        b.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                        i += 4;
                        break;
                    default: throw fail("bad escape");
                }
            }
        }
    }

    static Object parse(String text) {
        Parser p = new Parser(text);
        Object v = p.value();
        p.ws();
        if (p.i != text.length()) throw p.fail("trailing characters");
        return v;
    }

    static List<Object> readInput(String path, int nparams) throws IOException {
        List<Object> vals = new ArrayList<>();
        for (String line : Files.readAllLines(Paths.get(path), StandardCharsets.UTF_8)) {
            if (!line.isBlank()) vals.add(parse(line));
        }
        if (vals.size() != nparams)
            throw new IllegalArgumentException("expected " + nparams + " input lines, got " + vals.size());
        return vals;
    }

    // ------------------------------------------------------------ JSON -> Java

    static List<?> arr(Object v) {
        if (!(v instanceof List)) throw new IllegalArgumentException("input: expected array");
        return (List<?>) v;
    }
    static String num(Object v) {
        if (!(v instanceof Num)) throw new IllegalArgumentException("input: expected number");
        return ((Num) v).text;
    }
    static int toInt(Object v) { return (int) Long.parseLong(num(v)); }
    static long toLong(Object v) { return Long.parseLong(num(v)); }
    static double toDouble(Object v) { return Double.parseDouble(num(v)); }
    static boolean toBool(Object v) {
        if (!(v instanceof Boolean)) throw new IllegalArgumentException("input: expected boolean");
        return (Boolean) v;
    }
    static String toStr(Object v) {
        if (!(v instanceof String)) throw new IllegalArgumentException("input: expected string");
        return (String) v;
    }
    static char toChar(Object v) {
        String s = toStr(v);
        return s.isEmpty() ? '\0' : s.charAt(0);
    }

    static int[] intArr(Object v) { List<?> a = arr(v); int[] r = new int[a.size()]; for (int i = 0; i < r.length; i++) r[i] = toInt(a.get(i)); return r; }
    static long[] longArr(Object v) { List<?> a = arr(v); long[] r = new long[a.size()]; for (int i = 0; i < r.length; i++) r[i] = toLong(a.get(i)); return r; }
    static double[] doubleArr(Object v) { List<?> a = arr(v); double[] r = new double[a.size()]; for (int i = 0; i < r.length; i++) r[i] = toDouble(a.get(i)); return r; }
    static boolean[] boolArr(Object v) { List<?> a = arr(v); boolean[] r = new boolean[a.size()]; for (int i = 0; i < r.length; i++) r[i] = toBool(a.get(i)); return r; }
    static char[] charArr(Object v) { List<?> a = arr(v); char[] r = new char[a.size()]; for (int i = 0; i < r.length; i++) r[i] = toChar(a.get(i)); return r; }
    static String[] strArr(Object v) { List<?> a = arr(v); String[] r = new String[a.size()]; for (int i = 0; i < r.length; i++) r[i] = toStr(a.get(i)); return r; }

    static int[][] intArr2(Object v) { List<?> a = arr(v); int[][] r = new int[a.size()][]; for (int i = 0; i < r.length; i++) r[i] = intArr(a.get(i)); return r; }
    static long[][] longArr2(Object v) { List<?> a = arr(v); long[][] r = new long[a.size()][]; for (int i = 0; i < r.length; i++) r[i] = longArr(a.get(i)); return r; }
    static double[][] doubleArr2(Object v) { List<?> a = arr(v); double[][] r = new double[a.size()][]; for (int i = 0; i < r.length; i++) r[i] = doubleArr(a.get(i)); return r; }
    static boolean[][] boolArr2(Object v) { List<?> a = arr(v); boolean[][] r = new boolean[a.size()][]; for (int i = 0; i < r.length; i++) r[i] = boolArr(a.get(i)); return r; }
    static char[][] charArr2(Object v) { List<?> a = arr(v); char[][] r = new char[a.size()][]; for (int i = 0; i < r.length; i++) r[i] = charArr(a.get(i)); return r; }
    static String[][] strArr2(Object v) { List<?> a = arr(v); String[][] r = new String[a.size()][]; for (int i = 0; i < r.length; i++) r[i] = strArr(a.get(i)); return r; }

    static <T> List<T> list(Object v, Function<Object, T> f) {
        List<?> a = arr(v);
        List<T> r = new ArrayList<>(a.size());
        for (Object x : a) r.add(f.apply(x));
        return r;
    }

    static ListNode listNode(Object v) {
        ListNode dummy = new ListNode();
        ListNode tail = dummy;
        for (Object x : arr(v)) { tail.next = new ListNode(toInt(x)); tail = tail.next; }
        return dummy.next;
    }

    static TreeNode treeNode(Object v) {
        List<?> a = arr(v);
        if (a.isEmpty() || a.get(0) == null) return null;
        TreeNode root = new TreeNode(toInt(a.get(0)));
        ArrayDeque<TreeNode> q = new ArrayDeque<>();
        q.add(root);
        int i = 1;
        while (!q.isEmpty() && i < a.size()) {
            TreeNode cur = q.poll();
            if (i < a.size() && a.get(i) != null) { cur.left = new TreeNode(toInt(a.get(i))); q.add(cur.left); }
            i++;
            if (i < a.size() && a.get(i) != null) { cur.right = new TreeNode(toInt(a.get(i))); q.add(cur.right); }
            i++;
        }
        return root;
    }

    // ------------------------------------------------------------ Java -> JSON

    /** Typed writers: a null list or tree is an empty JSON array, like LeetCode. */
    static String writeListNode(ListNode v) { return v == null ? "[]" : write(v); }
    static String writeTreeNode(TreeNode v) { return v == null ? "[]" : write(v); }

    static String write(Object v) {
        StringBuilder b = new StringBuilder();
        put(b, v);
        return b.toString();
    }

    static void putStr(StringBuilder b, String s) {
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                case '\b': b.append("\\b"); break;
                case '\f': b.append("\\f"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        b.append('"');
    }

    static void putDouble(StringBuilder b, double d) {
        if (Double.isNaN(d) || Double.isInfinite(d)) b.append("null");
        else b.append(Double.toString(d));
    }

    static void put(StringBuilder b, Object v) {
        if (v == null) { b.append("null"); return; }
        if (v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte) { b.append(v.toString()); return; }
        if (v instanceof Double || v instanceof Float) { putDouble(b, ((Number) v).doubleValue()); return; }
        if (v instanceof Boolean) { b.append(((Boolean) v) ? "true" : "false"); return; }
        if (v instanceof Character) { putStr(b, String.valueOf((char) (Character) v)); return; }
        if (v instanceof String) { putStr(b, (String) v); return; }
        if (v instanceof ListNode) {
            b.append('[');
            int n = 0;
            for (ListNode p = (ListNode) v; p != null; p = p.next) {
                if (++n > 10_000_000) throw new IllegalStateException("output list is too long (cycle?)");
                if (n > 1) b.append(',');
                b.append(p.val);
            }
            b.append(']');
            return;
        }
        if (v instanceof TreeNode) {
            ArrayList<TreeNode> order = new ArrayList<>();
            order.add((TreeNode) v);
            for (int i = 0; i < order.size(); i++) {
                if (order.size() > 20_000_000) throw new IllegalStateException("output tree is too large (cycle?)");
                TreeNode t = order.get(i);
                if (t != null) { order.add(t.left); order.add(t.right); }
            }
            int end = order.size();
            while (end > 0 && order.get(end - 1) == null) end--;
            b.append('[');
            for (int i = 0; i < end; i++) {
                if (i > 0) b.append(',');
                TreeNode t = order.get(i);
                if (t == null) b.append("null"); else b.append(t.val);
            }
            b.append(']');
            return;
        }
        if (v instanceof int[]) { int[] a = (int[]) v; b.append('['); for (int i = 0; i < a.length; i++) { if (i > 0) b.append(','); b.append(a[i]); } b.append(']'); return; }
        if (v instanceof long[]) { long[] a = (long[]) v; b.append('['); for (int i = 0; i < a.length; i++) { if (i > 0) b.append(','); b.append(a[i]); } b.append(']'); return; }
        if (v instanceof double[]) { double[] a = (double[]) v; b.append('['); for (int i = 0; i < a.length; i++) { if (i > 0) b.append(','); putDouble(b, a[i]); } b.append(']'); return; }
        if (v instanceof boolean[]) { boolean[] a = (boolean[]) v; b.append('['); for (int i = 0; i < a.length; i++) { if (i > 0) b.append(','); b.append(a[i] ? "true" : "false"); } b.append(']'); return; }
        if (v instanceof char[]) { char[] a = (char[]) v; b.append('['); for (int i = 0; i < a.length; i++) { if (i > 0) b.append(','); putStr(b, String.valueOf(a[i])); } b.append(']'); return; }
        if (v instanceof Object[]) { Object[] a = (Object[]) v; b.append('['); for (int i = 0; i < a.length; i++) { if (i > 0) b.append(','); put(b, a[i]); } b.append(']'); return; }
        if (v instanceof Iterable) {
            b.append('[');
            boolean first = true;
            for (Object x : (Iterable<?>) v) { if (!first) b.append(','); first = false; put(b, x); }
            b.append(']');
            return;
        }
        throw new IllegalStateException("cannot serialize " + v.getClass().getName());
    }

    // ------------------------------------------------------------ results

    static long peakKb() {
        try (BufferedReader r = Files.newBufferedReader(Paths.get("/proc/self/status"))) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.startsWith("VmHWM:")) return Long.parseLong(line.replaceAll("[^0-9]", ""));
            }
        } catch (IOException | NumberFormatException e) {
            // not Linux, or no procfs
        }
        return 0;
    }

    static void report(PrintStream res, String id, String status, long ns, String message, String output) {
        StringBuilder b = new StringBuilder("{\"id\":");
        putStr(b, id);
        b.append(",\"status\":\"").append(status).append("\",\"time_ns\":").append(ns);
        b.append(",\"mem_kb\":").append(peakKb()).append(",\"message\":");
        putStr(b, message == null ? "" : message);
        b.append(",\"output\":").append(output == null ? "null" : output).append("}\n");
        res.print(b);
        res.flush();
    }

    static String trace(Throwable e) {
        StringWriter sw = new StringWriter();
        e.printStackTrace(new PrintWriter(sw));
        StringBuilder b = new StringBuilder();
        for (String line : sw.toString().split("\n")) {
            if (line.contains("at Main.") || line.contains("at java.base/java.lang.Thread.run")) break;
            b.append(line).append('\n');
            if (b.length() > 4000) break;
        }
        return b.toString().trim();
    }
}
