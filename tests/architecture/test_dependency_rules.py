"""Architecture tests для проверки dependency rules.

Этот модуль проверяет, что domain services и core utilities не импортируют
aiogram-типы, сохраняя чистоту слоёв архитектуры.
"""

import ast
import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Каталоги, в которых запрещён импорт aiogram
FORBIDDEN_AIOGRAM_DIRS = [
    "src/domains/*/service.py",
    "src/core/**/*.py",
]

# Исключения: файлы, где aiogram разрешён
ALLOWED_AIOGRAM_FILES = {
    "src/core/__init__.py",  # Может импортировать aiogram для сборки Router
}


def _find_python_files(pattern: str) -> list[Path]:
    """Находит Python-файлы по glob-паттерну.

    Args:
        pattern: Glob-паттерн для поиска файлов.

    Returns:
        Список найденных файлов.
    """
    project_root = Path(__file__).parent.parent.parent
    return list(project_root.glob(pattern))


def _has_aiogram_import(file_path: Path) -> tuple[bool, list[str]]:
    """Проверяет наличие aiogram-импортов в файле.

    Args:
        file_path: Путь к Python-файлу.

    Returns:
        Кортеж (has_import, import_lines) где has_import — флаг наличия импорта,
        import_lines — список строк с импортами aiogram.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError) as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return False, []

    aiogram_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("aiogram"):
                    aiogram_imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("aiogram"):
            names = ", ".join(alias.name for alias in node.names)
            aiogram_imports.append(f"from {node.module} import {names}")

    return bool(aiogram_imports), aiogram_imports


@pytest.mark.parametrize("pattern", FORBIDDEN_AIOGRAM_DIRS)
def test_domain_services_do_not_import_aiogram(pattern: str) -> None:
    """Проверяет, что domain services и core не импортируют aiogram.

    Args:
        pattern: Glob-паттерн для поиска файлов.
    """
    files = _find_python_files(pattern)
    violations = []

    for file_path in files:
        # Пропускаем разрешённые файлы
        relative_path = str(file_path.relative_to(Path(__file__).parent.parent.parent))
        if relative_path in ALLOWED_AIOGRAM_FILES:
            continue

        has_import, import_lines = _has_aiogram_import(file_path)
        if has_import:
            violations.append((relative_path, import_lines))

    if violations:
        error_msg = "Found aiogram imports in forbidden locations:\n"
        for file_path, imports in violations:
            error_msg += f"\n{file_path}:\n"
            for imp in imports:
                error_msg += f"  - {imp}\n"
        error_msg += "\nDomain services and core utilities must not import aiogram.\n"
        error_msg += "Use primitives (int, str, bytes) and domain models instead."
        pytest.fail(error_msg)


def test_shared_domain_contracts_explicitly_documented() -> None:
    """Проверяет, что shared/domain_contracts.py содержит документацию о границах aiogram."""
    project_root = Path(__file__).parent.parent.parent
    contracts_file = project_root / "src" / "shared" / "domain_contracts.py"

    if not contracts_file.exists():
        pytest.skip("shared/domain_contracts.py not found")

    source = contracts_file.read_text(encoding="utf-8")

    # Проверяем наличие ключевых фраз в документации
    required_phrases = [
        "Telegram-специфичные",
        "Границы aiogram-зависимости",
        "handlers",
        "service",
    ]

    missing_phrases = [phrase for phrase in required_phrases if phrase not in source]

    if missing_phrases:
        pytest.fail(
            f"shared/domain_contracts.py должен содержать документацию о границах aiogram.\n"
            f"Отсутствующие фразы: {missing_phrases}"
        )
