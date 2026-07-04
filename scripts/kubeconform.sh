#!/usr/bin/env bash
# Validate the rendered app-template output and the raw Flux/CRD manifests.
# Schema resolution: upstream k8s schemas, then home-operations' CRD schema
# mirror, then the datree CRD catalog as fallback.
set -euo pipefail

SCHEMA_ARGS=(
  -schema-location default
  -schema-location 'https://k8s-schemas.home-operations.com/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
)

VERSION=$(yq '.spec.ref.tag' deploy/kubernetes/app/ocirepository.yaml)
WORKDIR=$(mktemp -d)
trap 'rm -rf "${WORKDIR}"' EXIT
yq '.spec.values' deploy/kubernetes/app/helmrelease.yaml > "${WORKDIR}/values.yaml"

echo "--> rendering app-template ${VERSION} and validating workload manifests"
# pull first: helm 4 writes OCI pull progress to stdout, which would corrupt a
# piped template render
helm pull oci://ghcr.io/bjw-s-labs/helm/app-template --version "${VERSION}" -d "${WORKDIR}" >/dev/null
helm template resolute "${WORKDIR}/app-template-${VERSION}.tgz" -f "${WORKDIR}/values.yaml" \
  | kubeconform "${SCHEMA_ARGS[@]}" -strict -summary

echo "--> validating Flux/CRD manifests"
kubeconform "${SCHEMA_ARGS[@]}" -strict -summary \
  deploy/kubernetes/ks.yaml \
  deploy/kubernetes/app/pvc.yaml \
  deploy/kubernetes/app/externalsecret.yaml \
  deploy/kubernetes/app/helmrelease.yaml \
  deploy/kubernetes/app/ocirepository.yaml
