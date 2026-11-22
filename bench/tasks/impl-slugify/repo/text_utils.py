def slugify(text: str) -> str:
    """Turn text into a URL slug.

    - Accented letters become their plain ASCII letter ("é" becomes "e"); other non-ASCII characters are dropped.
    - Letters are lowercased.
    - Every run of characters that are not ASCII letters or digits becomes one hyphen.
    - The slug never starts or ends with a hyphen.
    - Text with no letters or digits gives "".
    """
    raise NotImplementedError
