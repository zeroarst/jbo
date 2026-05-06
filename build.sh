#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$(dirname "$0")/dist"
cd "$(dirname "$0")/src/jbo-handler"
GOOS=windows GOARCH=amd64 go build -ldflags="-H windowsgui -s -w" -o ../../dist/jbo-handler.exe .
echo "Built: dist/jbo-handler.exe"
