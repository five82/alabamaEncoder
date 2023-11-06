import os


class PathAlabama:
    def __init__(self, path: str):
        self.path = path

    def get(self):
        return self.path

    def get_safe(self):
        return f'"{self.path}"'

    def exists(self):
        return os.path.exists(self.path)

    def size_bytes(self):
        return os.path.getsize(self.path)

    def __str__(self):
        return self.get_safe()

    def check_video(self):
        if not self.exists():
            raise FileNotFoundError(f"File {self.path} does not exist")
        if self.size_bytes() < 10:
            raise ValueError(f"File {self.path} is too small to be a vaild media file")
        return True
