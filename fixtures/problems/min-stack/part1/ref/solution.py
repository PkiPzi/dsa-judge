class MinStack:
    def __init__(self):
        self.st = []
        self.mn = []

    def push(self, val: int) -> None:
        self.st.append(val)
        self.mn.append(val if not self.mn else min(self.mn[-1], val))

    def pop(self) -> None:
        self.st.pop()
        self.mn.pop()

    def top(self) -> int:
        return self.st[-1]

    def getMin(self) -> int:
        return self.mn[-1]
