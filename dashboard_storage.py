"""Keep expected persistence failures from stopping live dashboard rendering."""

import sqlite3


def storage_call(operation, *args, on_error, default=None, **kwargs):
    """Report only expected database/filesystem failures; return a UI fallback."""
    try:
        return operation(*args, **kwargs)
    except (sqlite3.ProgrammingError, sqlite3.IntegrityError, sqlite3.DataError,
            sqlite3.InterfaceError):
        raise
    except (sqlite3.Error, OSError) as error:
        on_error(f"History unavailable ({operation.__name__}): {error}. "
                 "Live monitoring continues; history may be incomplete.")
        return default
