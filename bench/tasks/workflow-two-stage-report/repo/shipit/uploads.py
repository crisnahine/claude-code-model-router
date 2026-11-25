from .settings import MAX_UPLOAD_MB


def accepts(size_bytes: int) -> bool:
    return 0 < size_bytes <= MAX_UPLOAD_MB * 1024 * 1024
