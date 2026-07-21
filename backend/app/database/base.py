"""Declarative base class every ORM model inherits from.

Kept in its own module (rather than in `session.py`) so Alembic's `env.py`
can import `Base.metadata` without also importing the engine.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
