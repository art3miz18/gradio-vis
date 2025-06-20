import os
class DummyUpload:
        def __init__(self, path: str):
            self.filename = os.path.basename(path)
            self._path = path

        async def read(self) -> bytes:
            with open(self._path, "rb") as f:
                return f.read()