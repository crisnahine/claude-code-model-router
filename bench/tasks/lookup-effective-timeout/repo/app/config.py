import configparser


def _read(path):
    parser = configparser.ConfigParser()
    return parser if parser.read(path) else None


def file_timeout(path):
    """Seconds from the [http] timeout key of an ini file, or None."""
    parser = _read(path)
    value = parser.get("http", "timeout", fallback=None) if parser else None
    return float(value) if value else None


def stream_default(path):
    """Whether responses stream unless the caller says otherwise: the [http] stream key, off when unset."""
    parser = _read(path)
    return parser.getboolean("http", "stream", fallback=False) if parser else False
