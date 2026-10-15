class LRUCache:
    """Obviously correct and slow: a list of [key, value] pairs, oldest first."""

    def __init__(self, capacity: int):
        self.cap = capacity
        self.items = []

    def _find(self, key):
        for i, (k, _) in enumerate(self.items):
            if k == key:
                return i
        return -1

    def get(self, key: int) -> int:
        i = self._find(key)
        if i < 0:
            return -1
        pair = self.items.pop(i)
        self.items.append(pair)
        return pair[1]

    def put(self, key: int, value: int) -> None:
        i = self._find(key)
        if i >= 0:
            self.items.pop(i)
        self.items.append([key, value])
        if len(self.items) > self.cap:
            self.items.pop(0)
