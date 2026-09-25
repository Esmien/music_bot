"""Совместимый импорт валидатора из нового доменного модуля."""

import sys

from domains.enricher import validator

sys.modules[__name__] = validator
