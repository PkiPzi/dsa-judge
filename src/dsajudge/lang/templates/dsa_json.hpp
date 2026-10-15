// dsa-judge: minimal JSON support for the C++ driver.
// Hand-written on purpose: compile time matters more than features here.
// Included after <bits/stdc++.h> and the ListNode/TreeNode definitions.
#pragma once

namespace dsa {

struct J {
    enum Kind { NUL, BOOL, NUM, STR, ARR } k = NUL;
    bool b = false;
    std::string s;  // number token text, or decoded string contents
    std::vector<J> a;
};

struct ParseError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct Parser {
    const char* p;
    const char* e;

    void ws() {
        while (p < e && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) ++p;
    }
    [[noreturn]] void fail(const char* what) { throw ParseError(std::string("bad JSON: ") + what); }

    static void utf8(std::string& out, unsigned cp) {
        if (cp < 0x80) {
            out += (char)cp;
        } else if (cp < 0x800) {
            out += (char)(0xC0 | (cp >> 6));
            out += (char)(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += (char)(0xE0 | (cp >> 12));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        } else {
            out += (char)(0xF0 | (cp >> 18));
            out += (char)(0x80 | ((cp >> 12) & 0x3F));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        }
    }

    unsigned hex4() {
        if (e - p < 4) fail("short \\u escape");
        unsigned v = 0;
        for (int i = 0; i < 4; ++i) {
            char c = *p++;
            v <<= 4;
            if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
            else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
            else fail("bad \\u escape");
        }
        return v;
    }

    std::string str() {
        ++p;  // opening quote
        std::string out;
        while (true) {
            if (p >= e) fail("unterminated string");
            char c = *p++;
            if (c == '"') break;
            if (c != '\\') {
                out += c;
                continue;
            }
            if (p >= e) fail("unterminated escape");
            char x = *p++;
            switch (x) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned cp = hex4();
                    if (cp >= 0xD800 && cp < 0xDC00 && e - p >= 6 && p[0] == '\\' && p[1] == 'u') {
                        p += 2;
                        unsigned lo = hex4();
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    utf8(out, cp);
                    break;
                }
                default: fail("bad escape");
            }
        }
        return out;
    }

    J value() {
        ws();
        if (p >= e) fail("unexpected end");
        J v;
        char c = *p;
        if (c == '[') {
            v.k = J::ARR;
            ++p;
            ws();
            if (p < e && *p == ']') {
                ++p;
                return v;
            }
            while (true) {
                v.a.push_back(value());
                ws();
                if (p < e && *p == ',') {
                    ++p;
                    continue;
                }
                if (p < e && *p == ']') {
                    ++p;
                    break;
                }
                fail("expected , or ]");
            }
        } else if (c == '"') {
            v.k = J::STR;
            v.s = str();
        } else if (c == 't' && e - p >= 4 && std::strncmp(p, "true", 4) == 0) {
            v.k = J::BOOL;
            v.b = true;
            p += 4;
        } else if (c == 'f' && e - p >= 5 && std::strncmp(p, "false", 5) == 0) {
            v.k = J::BOOL;
            p += 5;
        } else if (c == 'n' && e - p >= 4 && std::strncmp(p, "null", 4) == 0) {
            p += 4;
        } else if (c == '-' || (c >= '0' && c <= '9')) {
            v.k = J::NUM;
            const char* st = p;
            ++p;
            while (p < e && (std::isdigit((unsigned char)*p) || *p == '.' || *p == 'e' || *p == 'E' ||
                             *p == '+' || *p == '-'))
                ++p;
            v.s.assign(st, p);
        } else {
            fail("unexpected character");
        }
        return v;
    }
};

inline J parse(const std::string& text) {
    Parser ps{text.data(), text.data() + text.size()};
    J v = ps.value();
    ps.ws();
    if (ps.p != ps.e) ps.fail("trailing characters");
    return v;
}

inline std::vector<J> read_input(const std::string& path, size_t nparams) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + path);
    std::vector<J> vals;
    std::string line;
    while (std::getline(f, line)) {
        bool blank = true;
        for (char c : line)
            if (!std::isspace((unsigned char)c)) blank = false;
        if (!blank) vals.push_back(parse(line));
    }
    if (vals.size() != nparams)
        throw std::runtime_error("expected " + std::to_string(nparams) + " input lines, got " +
                                 std::to_string(vals.size()));
    return vals;
}

// ------------------------------------------------------------ JSON -> C++

template <class T>
struct conv;

inline const J& want(const J& j, J::Kind k, const char* what) {
    if (j.k != k) throw std::runtime_error(std::string("input: expected ") + what);
    return j;
}

template <>
struct conv<int> {
    static int get(const J& j) { return (int)std::strtoll(want(j, J::NUM, "integer").s.c_str(), nullptr, 10); }
};
template <>
struct conv<long long> {
    static long long get(const J& j) { return std::strtoll(want(j, J::NUM, "integer").s.c_str(), nullptr, 10); }
};
template <>
struct conv<double> {
    static double get(const J& j) { return std::strtod(want(j, J::NUM, "number").s.c_str(), nullptr); }
};
template <>
struct conv<bool> {
    static bool get(const J& j) { return want(j, J::BOOL, "boolean").b; }
};
template <>
struct conv<char> {
    static char get(const J& j) {
        const std::string& s = want(j, J::STR, "one-character string").s;
        return s.empty() ? '\0' : s[0];
    }
};
template <>
struct conv<std::string> {
    static std::string get(const J& j) { return want(j, J::STR, "string").s; }
};
template <class T>
struct conv<std::vector<T>> {
    static std::vector<T> get(const J& j) {
        want(j, J::ARR, "array");
        std::vector<T> out;
        out.reserve(j.a.size());
        for (const J& x : j.a) out.push_back(conv<T>::get(x));
        return out;
    }
};
template <>
struct conv<ListNode*> {
    static ListNode* get(const J& j) {
        want(j, J::ARR, "array for ListNode");
        ListNode dummy;
        ListNode* tail = &dummy;
        for (const J& x : j.a) {
            tail->next = new ListNode(conv<int>::get(x));
            tail = tail->next;
        }
        return dummy.next;
    }
};
template <>
struct conv<TreeNode*> {
    static TreeNode* get(const J& j) {
        want(j, J::ARR, "level-order array for TreeNode");
        if (j.a.empty() || j.a[0].k == J::NUL) return nullptr;
        TreeNode* root = new TreeNode(conv<int>::get(j.a[0]));
        std::deque<TreeNode*> q{root};
        size_t i = 1;
        while (!q.empty() && i < j.a.size()) {
            TreeNode* cur = q.front();
            q.pop_front();
            if (i < j.a.size() && j.a[i].k != J::NUL) {
                cur->left = new TreeNode(conv<int>::get(j.a[i]));
                q.push_back(cur->left);
            }
            ++i;
            if (i < j.a.size() && j.a[i].k != J::NUL) {
                cur->right = new TreeNode(conv<int>::get(j.a[i]));
                q.push_back(cur->right);
            }
            ++i;
        }
        return root;
    }
};

template <class T>
T from_json(const J& j) {
    return conv<T>::get(j);
}

// ------------------------------------------------------------ C++ -> JSON

inline void put_str(std::string& out, const std::string& s) {
    out += '"';
    for (char ch : s) {
        unsigned char c = (unsigned char)ch;
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += ch;
                }
        }
    }
    out += '"';
}

inline void put(std::string& out, int v) { out += std::to_string(v); }
inline void put(std::string& out, long long v) { out += std::to_string(v); }
inline void put(std::string& out, bool v) { out += v ? "true" : "false"; }
inline void put(std::string& out, char v) { put_str(out, std::string(1, v)); }
inline void put(std::string& out, const std::string& v) { put_str(out, v); }
inline void put(std::string& out, double v) {
    if (!std::isfinite(v)) {
        out += "null";
        return;
    }
    char buf[64];
    auto r = std::to_chars(buf, buf + sizeof buf, v);
    out.append(buf, r.ptr);
}
inline void put(std::string& out, ListNode* head) {
    out += '[';
    size_t n = 0;
    for (ListNode* p = head; p; p = p->next) {
        if (++n > 10000000) throw std::runtime_error("output list is too long (cycle?)");
        if (n > 1) out += ',';
        put(out, p->val);
    }
    out += ']';
}
inline void put(std::string& out, TreeNode* root) {
    std::vector<TreeNode*> order;
    if (root) order.push_back(root);
    for (size_t i = 0; i < order.size(); ++i) {
        if (order.size() > 20000000) throw std::runtime_error("output tree is too large (cycle?)");
        TreeNode* t = order[i];
        if (t) {
            order.push_back(t->left);
            order.push_back(t->right);
        }
    }
    while (!order.empty() && !order.back()) order.pop_back();
    out += '[';
    for (size_t i = 0; i < order.size(); ++i) {
        if (i) out += ',';
        if (order[i]) put(out, order[i]->val);
        else out += "null";
    }
    out += ']';
}
inline void put(std::string& out, const std::vector<bool>& v) {
    out += '[';
    for (size_t i = 0; i < v.size(); ++i) {
        if (i) out += ',';
        put(out, (bool)v[i]);
    }
    out += ']';
}
template <class T>
void put(std::string& out, const std::vector<T>& v) {
    out += '[';
    for (size_t i = 0; i < v.size(); ++i) {
        if (i) out += ',';
        put(out, v[i]);
    }
    out += ']';
}

// ------------------------------------------------------------ results

struct Timer {
    std::chrono::steady_clock::time_point t0{}, t1{};
    bool started = false, stopped = false;
    void start() {
        started = true;
        t0 = std::chrono::steady_clock::now();
    }
    void stop() {
        t1 = std::chrono::steady_clock::now();
        stopped = true;
    }
    long long ns() const {
        if (!started) return 0;
        auto end = stopped ? t1 : std::chrono::steady_clock::now();
        return (long long)std::chrono::duration_cast<std::chrono::nanoseconds>(end - t0).count();
    }
};

// Peak resident memory since exec. getrusage()'s ru_maxrss is not used first because the kernel
// folds the *parent's* peak into a child's value across exec, so a big judge process would make
// every solution look big.
inline long peak_kb() {
    if (FILE* f = fopen("/proc/self/status", "r")) {
        char line[256];
        long kb = -1;
        while (fgets(line, sizeof line, f)) {
            if (strncmp(line, "VmHWM:", 6) == 0) {
                kb = strtol(line + 6, nullptr, 10);
                break;
            }
        }
        fclose(f);
        if (kb >= 0) return kb;
    }
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    return ru.ru_maxrss;
}

inline void report_ns(FILE* res, const std::string& id, const char* status, long long ns,
                      const std::string& message, const std::string* output) {
    std::string line = "{\"id\":";
    put_str(line, id);
    line += ",\"status\":\"";
    line += status;
    line += "\",\"time_ns\":" + std::to_string(ns);
    line += ",\"mem_kb\":" + std::to_string(peak_kb());
    line += ",\"message\":";
    put_str(line, message);
    line += ",\"output\":";
    line += output ? *output : std::string("null");
    line += "}\n";
    std::fwrite(line.data(), 1, line.size(), res);
    std::fflush(res);
}

inline void report(FILE* res, const std::string& id, const char* status, const Timer& t,
                   const std::string& message, const std::string* output) {
    report_ns(res, id, status, t.ns(), message, output);
}

}  // namespace dsa
