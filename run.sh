#!/usr/bin/env sh
# One command to run the agent on a list of Norwegian organisation numbers.
#   ./run.sh companies.txt            -> profiles/<orgnr>.json + runs/<timestamp>.json
#   ./run.sh 923609016 878703812      -> same, for numbers given inline
# Defaults enforce the daily limits: 2000 outbound requests, 45 minutes, $8 declared LLM spend.
set -e
cd "$(dirname "$0")"
python -m pip install -q -r requirements.txt
if [ -f "$1" ]; then
  exec python -m signalpost run --input "$1" "${@:2}"
else
  exec python -m signalpost run "$@"
fi
