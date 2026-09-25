"""Совместимый импорт FSM-состояний из нового доменного модуля."""

import sys

from domains.enricher import fsm

sys.modules[__name__] = fsm
