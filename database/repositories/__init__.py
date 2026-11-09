"""Data-access helpers. Services use SQLAlchemy sessions directly through these helpers;
all authorization is enforced in the service layer, never here."""
from database.repositories.base import get_active, get_or_raise  # noqa: F401
