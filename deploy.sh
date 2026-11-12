#!/usr/bin/env bash
# Обновление CargoFlow на сервере.
#
#   ./deploy.sh            — забрать код, пересобрать образ только если менялись
#                            requirements.txt или Dockerfile, перезапустить приложение
#   ./deploy.sh --build    — пересобрать образ принудительно
#   ./deploy.sh --migrate  — после запуска выполнить alembic upgrade head
#
# База (db) не пересоздаётся, pgAdmin на сервере не поднимается (профиль tools).

set -euo pipefail
cd "$(dirname "$0")"

FORCE_BUILD=0
MIGRATE=0
for arg in "$@"; do
    case "$arg" in
        --build)   FORCE_BUILD=1 ;;
        --migrate) MIGRATE=1 ;;
        *) echo "Неизвестный параметр: $arg" >&2; exit 1 ;;
    esac
done

# docker compose (v2) или старый docker-compose
if docker compose version >/dev/null 2>&1; then
    DC=(docker compose --profile prod)
else
    DC=(docker-compose --profile prod)
    export DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1
fi

echo "==> Забираю изменения"
OLD_HEAD=$(git rev-parse HEAD)
git pull --ff-only
NEW_HEAD=$(git rev-parse HEAD)

if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
    echo "    Новых коммитов нет"
else
    git --no-pager log --oneline "$OLD_HEAD..$NEW_HEAD"
fi

# Образ нужно пересобирать, только если изменились зависимости или Dockerfile:
# сам код смонтирован в контейнер томом.
NEED_BUILD=$FORCE_BUILD
if [ "$NEED_BUILD" = 0 ] && [ "$OLD_HEAD" != "$NEW_HEAD" ]; then
    if ! git diff --quiet "$OLD_HEAD" "$NEW_HEAD" -- requirements.txt Dockerfile .dockerignore; then
        NEED_BUILD=1
    fi
fi
# Образа ещё нет (первый запуск) — тоже собираем
if [ "$NEED_BUILD" = 0 ] && [ -z "$("${DC[@]}" images -q app 2>/dev/null)" ]; then
    NEED_BUILD=1
fi

if [ "$NEED_BUILD" = 1 ]; then
    echo "==> Пересобираю образ приложения"
    "${DC[@]}" build app
    echo "==> Запускаю на новом образе"
    "${DC[@]}" up -d
else
    echo "==> Образ не менялся, перезапускаю приложение"
    "${DC[@]}" up -d
    "${DC[@]}" restart app
fi

# pgAdmin на сервере не нужен: убираем, если остался от старой конфигурации
if docker ps -a --format '{{.Names}}' | grep -qx cargoflow_pgadmin; then
    echo "==> Удаляю старый контейнер pgAdmin"
    docker rm -f -v cargoflow_pgadmin >/dev/null
fi

if [ "$MIGRATE" = 1 ]; then
    echo "==> Применяю миграции"
    "${DC[@]}" exec -T app alembic upgrade head
fi

echo "==> Состояние"
"${DC[@]}" ps
echo "==> Последние строки лога приложения"
sleep 3
"${DC[@]}" logs --tail=20 app
