from .config import file_timeout
from .defaults import DEFAULT_TIMEOUT_S
from .env import env_timeout


def resolve_timeout(environ, ini_path="settings.ini"):
    """The configured timeout: the environment, then the ini file, then the default."""
    for value in (env_timeout(environ), file_timeout(ini_path)):
        if value is not None:
            return value
    return DEFAULT_TIMEOUT_S
