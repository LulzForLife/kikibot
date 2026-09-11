#!/usr/bin/env bash

if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "Notice: .venv not found. Running with system Python."
fi

python -X jit -OO uci.py
