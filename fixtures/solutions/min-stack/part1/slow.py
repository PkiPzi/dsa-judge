class MinStack:
    def __init__(self):
        self.st = []

    def push(self, val: int) -> None:
        self.st.append(val)

    def pop(self) -> None:
        self.st.pop()

    def top(self) -> int:
        return self.st[-1]

    def getMin(self) -> int:
        tmp = []
        best = self.st[-1]
        while self.st:
            x = self.st.pop()
            if x < best:
                best = x
            tmp.append(x)
        while tmp:
            self.st.append(tmp.pop())
        return best
