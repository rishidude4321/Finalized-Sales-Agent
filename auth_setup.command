#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python auth_graph.py
read -p "Press Enter to close..."