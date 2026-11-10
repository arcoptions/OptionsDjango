#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Promote the exact Oryx artifact already accepted in staging.

set -euo pipefail

readonly REQUIRED_FILES=(
  ".ostype"
  "requirements.txt"
  "output.tar.zst"
  "oryx-manifest.toml"
)
WORK_DIR=""

err() {
  printf "ERROR: %s\n" "$1" >&2
  exit 1
}

require_environment() {
  local name="$1"
  [[ -n "${!name:-}" ]] || err "${name} must be set"
}

kudu_url() {
  local app_name="$1"
  printf "https://%s.scm.azurewebsites.net/api/vfs/site/wwwroot" \
    "${app_name}"
}

main() {
  require_environment "RESOURCE_GROUP"
  require_environment "STAGING_APP"
  require_environment "PRODUCTION_APP"
  require_environment "EXPECTED_SHA"

  command -v az >/dev/null || err "az is required"
  command -v curl >/dev/null || err "curl is required"
  command -v jq >/dev/null || err "jq is required"
  command -v sha256sum >/dev/null || err "sha256sum is required"

  local staging_url="https://${STAGING_APP}.azurewebsites.net"
  local staging_kudu
  local production_kudu
  staging_kudu="$(kudu_url "${STAGING_APP}")"
  production_kudu="$(kudu_url "${PRODUCTION_APP}")"

  WORK_DIR="$(mktemp -d)"
  trap 'rm -rf "${WORK_DIR}"' EXIT

  curl --fail --silent --show-error --max-time 30 \
    "${staging_url}/deployment-health/" \
    --output "${WORK_DIR}/staging-health.json"
  jq --exit-status --arg sha "${EXPECTED_SHA}" \
    '.status == "ok" and .commit == $sha' \
    "${WORK_DIR}/staging-health.json" >/dev/null ||
    err "staging does not report the expected commit"

  local token
  token="$(az account get-access-token \
    --resource https://management.azure.com/ \
    --query accessToken \
    --output tsv)"
  [[ -n "${token}" ]] || err "Azure access token is unavailable"

  local file_name
  for file_name in "${REQUIRED_FILES[@]}"; do
    curl --fail --silent --show-error --max-time 600 \
      --header "Authorization: Bearer ${token}" \
      "${staging_kudu}/${file_name}" \
      --output "${WORK_DIR}/${file_name}"
    [[ -s "${WORK_DIR}/${file_name}" ]] ||
      err "staging artifact ${file_name} is empty"
  done

  local local_hash
  local_hash="$(sha256sum "${WORK_DIR}/output.tar.zst" | cut -d' ' -f1)"

  for file_name in "${REQUIRED_FILES[@]}"; do
    curl --fail --silent --show-error --max-time 600 \
      --request PUT \
      --header "Authorization: Bearer ${token}" \
      --header "If-Match: *" \
      --header "Content-Type: application/octet-stream" \
      --upload-file "${WORK_DIR}/${file_name}" \
      "${production_kudu}/${file_name}" >/dev/null
  done

  local command_body
  local command_result
  command_body="$(jq -n \
    --arg command "sha256sum /home/site/wwwroot/output.tar.zst" \
    '{command: $command, dir: "/tmp"}')"
  command_result="$(curl --fail --silent --show-error --max-time 60 \
    --request POST \
    --header "Authorization: Bearer ${token}" \
    --header "Content-Type: application/json" \
    --data "${command_body}" \
    "https://${PRODUCTION_APP}.scm.azurewebsites.net/api/command")"
  [[ "$(jq -r '.ExitCode' <<<"${command_result}")" == "0" ]] ||
    err "production artifact checksum command failed"
  grep --fixed-strings --quiet "${local_hash}" \
    <<<"$(jq -r '.Output' <<<"${command_result}")" ||
    err "production artifact checksum does not match staging"

  az webapp restart \
    --resource-group "${RESOURCE_GROUP}" \
    --name "${PRODUCTION_APP}"
  printf "Promoted staging artifact SHA-256 %s\n" "${local_hash}"
}

main "$@"
