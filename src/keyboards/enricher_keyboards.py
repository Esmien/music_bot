"""Совместимый импорт клавиатур из нового доменного модуля."""

import sys

from domains.enricher import keyboards

sys.modules[__name__] = keyboards
