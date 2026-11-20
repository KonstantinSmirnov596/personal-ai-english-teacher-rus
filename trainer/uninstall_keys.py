"""Deletes this app's API keys from the Windows Credential Manager (``uninstall.cmd``).

Runs only when the user confirmed deleting their data. Connection ids come from the database;
ids just above the highest one are tried as well, in case a key outlived its row.
"""

from __future__ import annotations

import sqlite3
import sys

from trainer import keystore
from trainer.paths import data_dir


def connection_ids() -> list[int]:
    db = data_dir() / "trainer.db"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return [int(r[0]) for r in con.execute("SELECT id FROM connection")]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def main() -> int:
    ids = connection_ids()
    top = max(ids, default=0) + 20
    keystore.delete_all(sorted(set(ids) | set(range(1, top + 1))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
