PRELUDE = """
from typing import *
import collections, heapq, math, bisect, itertools, functools, string, re, random, operator
from collections import *
from heapq import *
from bisect import *
from itertools import *
from functools import *
inf = math.inf
"""


class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next


class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right


def _parse_type(t):
    depth = t.count("[]")
    return t.replace("[]", ""), depth


def _to_list_node(v):
    dummy = tail = ListNode()
    for x in v:
        tail.next = ListNode(x)
        tail = tail.next
    return dummy.next


def _to_tree_node(v):
    if not v or v[0] is None:
        return None
    root = TreeNode(v[0])
    queue, i = [root], 1
    head = 0
    while head < len(queue) and i < len(v):
        cur = queue[head]
        head += 1
        if i < len(v) and v[i] is not None:
            cur.left = TreeNode(v[i])
            queue.append(cur.left)
        i += 1
        if i < len(v) and v[i] is not None:
            cur.right = TreeNode(v[i])
            queue.append(cur.right)
        i += 1
    return root


def _from_list_node(node):
    out = []
    while node is not None:
        out.append(node.val)
        if len(out) > 10_000_000:
            raise ValueError("output list is too long (cycle?)")
        node = node.next
    return out


def _from_tree_node(root):
    order = [root] if root is not None else []
    i = 0
    while i < len(order):
        t = order[i]
        i += 1
        if t is not None:
            order.append(t.left)
            order.append(t.right)
        if len(order) > 20_000_000:
            raise ValueError("output tree is too large (cycle?)")
    while order and order[-1] is None:
        order.pop()
    return [None if t is None else t.val for t in order]


def _scalar_in(base, v):
    return float(v) if base == "double" else v


def _scalar_out(base, v):
    if base in ("int", "long"):
        return int(v)
    if base == "double":
        return float(v)
    if base == "bool":
        return bool(v)
    return v


def convert_in(t, v):
    if t == "ListNode":
        return _to_list_node(v)
    if t == "TreeNode":
        return _to_tree_node(v)
    base, depth = _parse_type(t)
    if base != "double":
        return v
    if depth == 0:
        return float(v)
    if depth == 1:
        return [float(x) for x in v]
    return [[float(x) for x in row] for row in v]


def convert_out(t, v):
    if t == "ListNode":
        return _from_list_node(v)
    if t == "TreeNode":
        return _from_tree_node(v)
    base, depth = _parse_type(t)
    if depth == 0:
        return _scalar_out(base, v)
    if depth == 1:
        return [_scalar_out(base, x) for x in v]
    return [[_scalar_out(base, x) for x in row] for row in v]


def peak_kb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def load_solution(path):
    mod = types.ModuleType("solution")
    mod.__file__ = path
    exec(PRELUDE, mod.__dict__)
    mod.__dict__["ListNode"] = ListNode
    mod.__dict__["TreeNode"] = TreeNode
    with open(path, encoding="utf-8") as f:
        src = f.read()
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


def solution_trace(exc, sol_path):
    frames = traceback.extract_tb(exc.__traceback__)
    keep = [f for f in frames if f.filename == sol_path] or frames[-1:]
    lines = traceback.format_list(keep)
    lines += traceback.format_exception_only(type(exc), exc)
    return "Traceback (most recent call last):\n" + "".join(lines).rstrip()


