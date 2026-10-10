
#!/usr/bin/env bash
# One-time local setup: venv, packages, .env, VS Code config, test library.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
mkdir -p .vscode
for f in vscode-template/*.json; do
  [ -f ".vscode/$(basename "$f")" ] || cp "$f" .vscode/
done
.venv/bin/python scripts/make_test_library.py
echo
echo "Done. In VS Code press F5 (\"Frame (dev server)\") and open http://127.0.0.1:8080"
