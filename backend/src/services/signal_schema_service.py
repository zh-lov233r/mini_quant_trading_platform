"""Read-only deployment checks; schema installation is an explicit operator action."""
from sqlalchemy import inspect
from src.models.tables import Base


def missing_signal_tables(db):
    required={name for name in Base.metadata.tables if name.startswith("signal_")}
    return sorted(required-set(inspect(db.connection()).get_table_names()))
