class UnsafeArchive(ValueError):
    """An archive holding a member that would write or point outside the destination, a kind of member we don't
    extract, or more than the limits allow."""


class LinkEscape(UnsafeArchive):
    """A symbolic link, followed through the archive's other links, that leads outside the destination."""
