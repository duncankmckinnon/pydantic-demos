#!/bin/sh
set -eu

image="${RUBY_OTEL_IMAGE:-pydantic-demos-ruby-otel:local}"
container_id=""

cleanup() {
  if [ -n "$container_id" ]; then
    docker stop "$container_id" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

container_id="$(docker run --detach --rm \
  --publish 127.0.0.1::9292 \
  --env OTEL_TRACES_EXPORTER=none \
  --env OTEL_METRICS_EXPORTER=none \
  --env OTEL_LOGS_EXPORTER=none \
  --env PYDANTIC_AI_GATEWAY_API_KEY=smoke-test \
  "$image")"

port="$(docker port "$container_id" 9292/tcp | sed 's/.*://')"
base_url="http://127.0.0.1:$port"

attempt=0
until curl --fail --silent "$base_url/health" >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    docker logs "$container_id"
    exit 1
  fi
  sleep 0.25
done

curl --fail --silent "$base_url/work" | grep --quiet '"status":"completed"'
error_status="$(curl --silent --output /tmp/ruby-otel-error-response.json --write-out '%{http_code}' "$base_url/error")"
[ "$error_status" = "500" ]
grep --quiet '"error":"intentional demo failure"' /tmp/ruby-otel-error-response.json
