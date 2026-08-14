#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
nohup python run.py > /tmp/sales_agent.log 2>&1 &
echo "Sales Support Agent started. You can close this window."