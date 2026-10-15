class MinStack:
    def __init__(self):
        self.st = []
        self.best = None

    def push(self, val: int) -> None:
        self.st.append(val)
        self.best = val if self.best is None else min(self.best, val)

    def pop(self) -> None:
        self.st.pop()

    def top(self) -> int:
        return self.st[-1]

    def getMin(self) -> int:
        return self.best
