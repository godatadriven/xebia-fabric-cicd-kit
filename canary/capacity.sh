#!/usr/bin/env bash
# Resume or pause the canary capacity: capacity.sh resume|pause
set -euo pipefail

action="${1:-}"
if [[ "${action}" != "resume" && "${action}" != "pause" ]]; then
  echo "usage: capacity.sh resume|pause" >&2
  exit 2
fi
api="2023-11-01"
url="https://management.azure.com${CAPACITY_RESOURCE_ID}"

state() {
  az rest --method get --url "${url}?api-version=${api}" --query properties.state -o tsv
}

if [[ "${action}" == "resume" && "$(state)" != "Active" ]]; then
  az rest --method post --url "${url}/resume?api-version=${api}"
elif [[ "${action}" == "pause" && "$(state)" != "Paused" ]]; then
  az rest --method post --url "${url}/suspend?api-version=${api}"
fi

target=$([[ "${action}" == "resume" ]] && echo Active || echo Paused)
for _ in $(seq 1 60); do
  [[ "$(state)" == "${target}" ]] && exit 0
  sleep 10
done
echo "Capacity did not reach ${target} within 10 minutes." >&2
exit 1
