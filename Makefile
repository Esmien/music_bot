.PHONY: dc-dev-run dc-dev-stop

dc-dev-run:
	docker compose --env-file ./.env -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up

dc-dev-stop:
	docker compose --env-file ./.env -f infra/docker-compose.yml down