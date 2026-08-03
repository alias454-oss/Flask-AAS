#!/usr/bin/env bash
set -euo pipefail

PIP_VERSION="${PIP_VERSION:-26.1.2}"
PIP_TOOLS_VERSION="${PIP_TOOLS_VERSION:-7.6.0}"

python -m pip install --upgrade \
  "pip==${PIP_VERSION}" \
  setuptools \
  wheel \
  "pip-tools==${PIP_TOOLS_VERSION}"

python -m piptools compile \
  --upgrade \
  --generate-hashes \
  --strip-extras \
  --output-file requirements.txt \
  pyproject.toml
