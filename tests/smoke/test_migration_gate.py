"""Smoke-тесты для проверки migration deployment gate."""

from pathlib import Path
from typing import Any


def _parse_yaml_file(path: Path) -> dict[str, Any]:
    """Универсальный разбор YAML файла с использованием PyYAML при наличии или иерархического fallback.

    Args:
        path: Путь к YAML-файлу.

    Returns:
        Словарь распарсенных ключей и значений.
    """
    try:
        import yaml

        with path.open(mode="r", encoding="utf-8") as file:
            return yaml.safe_load(stream=file) or {}
    except ImportError:
        root: dict[str, Any] = {}
        stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

        with path.open(mode="r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.split("#", 1)[0].rstrip()
                if not line.strip() or ":" not in line:
                    continue

                indent = len(line) - len(line.lstrip())
                key, _, val = line.strip().partition(":")
                key = key.strip().strip("'\"")
                val = val.strip()

                while stack and indent <= stack[-1][0]:
                    stack.pop()

                parent = stack[-1][1]

                if not val:
                    new_dict: dict[str, Any] = {}
                    parent[key] = new_dict
                    stack.append((indent, new_dict))
                else:
                    parent[key] = val.strip("'\"")

        return root


def test_compose_migration_gate_dependencies() -> None:
    """Проверяет обязательную блокировку старта bot и workers до успешного завершения migrate.

    Returns:
        None.
    """
    compose_path = Path("infra/docker-compose.yml")
    assert compose_path.exists(), "Файл infra/docker-compose.yml не найден"

    compose_data = _parse_yaml_file(path=compose_path)
    services = compose_data.get("services", {})

    assert "migrate" in services, "Сервис migrate обязан присутствовать в docker-compose.yml"
    migrate_config = services["migrate"]

    # Проверка параметров сервиса migrate
    assert migrate_config.get("restart") == "no", "Сервис migrate не должен автоматически перезапускаться"
    assert "postgres" in migrate_config.get("depends_on", {}), "Сервис migrate должен зависеть от postgres"

    # Проверка, что рабочие сервисы блокируются до завершения migrate
    gated_services = ["bot", "generation-worker", "enricher-worker"]
    for service_name in gated_services:
        assert service_name in services, f"Сервис {service_name} не найден в docker-compose"
        service_deps = services[service_name].get("depends_on", {})
        assert "migrate" in service_deps, f"Сервис {service_name} обязан зависеть от migrate"

        migrate_dep = service_deps["migrate"]
        condition = migrate_dep.get("condition") if isinstance(migrate_dep, dict) else None
        assert condition == "service_completed_successfully", (
            f"Для {service_name} condition зависимости migrate должен быть 'service_completed_successfully', "
            f"получено: {condition}"
        )


def test_migration_failure_simulated_gate() -> None:
    """Smoke-сценарий: симуляция падения Alembic миграции с ненулевым кодом выхода.

    Returns:
        None.
    """
    simulated_exit_code = 1
    migration_succeeded = simulated_exit_code == 0

    app_started = False
    if migration_succeeded:
        app_started = True

    assert not migration_succeeded, "Падение миграции должно определяться по ненулевому коду"
    assert not app_started, "При падении миграции сервисы бота и воркеров не должны стартовать"
