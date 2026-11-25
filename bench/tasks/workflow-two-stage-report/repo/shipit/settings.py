import os

MAX_UPLOAD_MB = int(os.environ.get("SHIPIT_MAX_UPLOAD_MB", "25"))
# The v1 API's limit; nothing reads it since the v2 upload path.
LEGACY_MAX_UPLOAD_MB = 10
