#!/bin/sh
cd "$(dirname "$0")" || exit 1
python3 install.py init && python3 launcher.py start --open
