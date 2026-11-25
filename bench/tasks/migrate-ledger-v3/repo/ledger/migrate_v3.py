"""Migrate a v2 export to the v3 format.

migrate(src, accounts, dst) reads the v2 export at src (docs/FORMAT_V2.md) and the accounts file at accounts
(data/accounts.json shows the shape), writes the v3 file at dst (docs/FORMAT_V3.md, "Migrating from v2") and
returns the number of records written.
"""
from .errors import MigrationError  # noqa: F401


def migrate(src, accounts, dst):
    raise NotImplementedError
