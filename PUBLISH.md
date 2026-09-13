# Publish checklist — Chainsmith

1. cd ~/downloads/ship/chainsmith
2. git init -b main
3. git add .
4. ./.pre-commit-secrets.sh
5. git commit -m "v1.0 — Chainsmith MILP factory planner"

## GitHub
1. Create PRIVATE repo on GitHub: chainsmith
2. git remote add origin git@github.com:USERNAME/chainsmith.git
3. git push -u origin main
4. Flip public: Settings → Visibility → Public

## Local deployment
    pip install -r requirements.txt
    waitress-serve --host=0.0.0.0 --port=5000 app:app

## Cloud options
- Fly.io — fly launch
- Railway — push repo
- Render — Python web service
- Hugging Face Spaces

## Commercial
- Free: factory-engine-v2 (heuristic) on GitHub
- Pro: $29 one-time MILP engine (Gumroad)
- Cloud: $9/month hosted Chainsmith API
