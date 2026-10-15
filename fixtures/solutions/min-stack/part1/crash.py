class MinStack:
    def __init__(self):
        self.st = []

    def push(self, val: int) -> None:
        self.st.append(val)

    def pop(self) -> None:
        self.st.pop()

    def top(self) -> int:
        raise RuntimeError("top() is broken")

    def getMin(self) -> int:
        return min(self.st)
