"""The cache every service shares.

A cached value lives under a key and carries tags naming what it was built from. A write records a Change in its
unit of work; when the unit commits, the cache drops every value carrying any tag the changes name.
"""
from .client import Cache
from .unit import UnitOfWork

__all__ = ["Cache", "UnitOfWork"]
