"""Conftest для tests/ — реэкспорт фикстур из src/conftest.py."""

from src.conftest import inmemory_broker  # noqa: F401

__all__ = ["inmemory_broker"]
