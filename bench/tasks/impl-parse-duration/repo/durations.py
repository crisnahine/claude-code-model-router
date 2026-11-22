def parse_duration(text: str) -> int:
    """Return the number of seconds in a duration such as "1h30m".

    - A duration is one or more parts. Each part is a whole number followed by a unit:
      d (86400 seconds), h (3600), m (60) or s (1).
    - Units appear in the order d, h, m, s, each at most once.
    - No spaces, signs or decimal points.
    - Anything else raises ValueError, the empty string included.
    """
    raise NotImplementedError
