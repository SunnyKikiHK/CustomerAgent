#!/usr/bin/env bash

# -e: the moment any command returns a non-zero exit code, the script aborts.
# -u: reading an undefined variable is a fatal error.
# -o pipefail: the exit code of a pipeline is the exit code of the first command that fails. If cat fails, the pipeline returns the failure exit code.
set -euo pipefail

# Load environment variables from config.sh
# SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" 
SCRIPT_DIR="."
if [[ -f "${SCRIPT_DIR}/config.sh" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/config.sh"
    set +a
fi

# Bring the stack up
# Pass --env-file so docker compose picks up NEXTAUTH_SECRET and SALT
# (config.sh only exports to the parent shell; child docker compose process needs .env)
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d --wait

# Wait for postgres to be healthy
echo "Waiting for postgres to be healthy..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' agent_postgres)" = "healthy" ]; do
    sleep 1
done
echo "Postgres is healthy."

# Show running services
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env ps

# Probe redis
echo -n "Redis ping: "
docker exec agent_redis redis-cli ping

# Wait for Temporal to accept CLI requests.
echo "Waiting for temporal to be ready..."
for _ in {1..60}; do
    if docker exec agent_temporal sh -c 'temporal --address "$(hostname -i):7233" operator namespace describe --namespace default' >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! docker exec agent_temporal sh -c 'temporal --address "$(hostname -i):7233" operator namespace describe --namespace default' >/dev/null 2>&1; then
    echo "Temporal did not become ready in time. Recent logs:"
    docker logs agent_temporal --tail 80
    exit 1
fi

echo "Temporal is ready."
echo "Temporal namespace status:"
docker exec agent_temporal sh -c 'temporal --address "$(hostname -i):7233" operator namespace describe --namespace default'

# Probe langfuse
echo "Langfuse health:"
curl -s http://localhost:3000/api/public/health
echo