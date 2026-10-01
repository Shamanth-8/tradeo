#!/usr/bin/env bash
# FlyWire FAFB v783 connectome (CC-BY-4.0), for backend/ml/flybrain.
# ~880 MB. Resumable: rerun after an interrupted download.
set -euo pipefail
dir="$(cd "$(dirname "$0")/.." && pwd)/data/connectome"
mkdir -p "$dir"
curl -fSL -C - -o "$dir/Supplemental_file1_neuron_annotations.tsv" \
  https://raw.githubusercontent.com/flyconnectome/flywire_annotations/main/supplemental_files/Supplemental_file1_neuron_annotations.tsv
curl -fSL -C - -o "$dir/proofread_connections_783.feather" \
  https://zenodo.org/api/records/10676866/files/proofread_connections_783.feather/content
echo "connectome ready in $dir"
