"""Совместимый импорт сервиса обогащения из нового доменного модуля."""

import sys

from domains.enricher import service

sys.modules[__name__] = service
