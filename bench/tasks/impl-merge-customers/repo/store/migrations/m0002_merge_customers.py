"""Make customers.email unique, ignoring ASCII case.

Customers whose emails are equal after trimming surrounding spaces and ignoring ASCII case are one person. Keep the
one with the earliest created_at (the lowest id on a tie), move every order and address of the others to it, and
delete the others. If the kept customer's name is NULL or empty, give it the name of the earliest-created duplicate
that has one. Store every customer's email trimmed, with ASCII letters lowercased; other characters stay as they are.

Afterwards the customers table declares email as `email TEXT NOT NULL COLLATE NOCASE UNIQUE`. Everything else in the
schema stays as it is: the same tables and columns, the foreign keys with ON DELETE CASCADE, the index and the view.
Every order and address survives. conn comes from store.db.connect, with foreign keys on and no open transaction;
leave foreign keys on, commit your work, and set user_version to 2.
"""


def upgrade(conn):
    raise NotImplementedError
