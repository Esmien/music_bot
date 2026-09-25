"""Совместимый импорт хендлеров обогащения из доменного модуля."""

import sys

from domains.enricher import handlers

sys.modules[__name__] = handlers
