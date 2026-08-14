#!/bin/bash
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Opening download page..."
    open https://www.python.org/downloads/
    read -p "Install Python 3, then press Enter to continue..."
fi

python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "A .env file was created. Open it and fill in your keys."
fi

echo "Setup complete."
read -p "Press Enter to close..."