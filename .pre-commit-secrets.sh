#!/data/data/com.termux/files/usr/bin/bash
# pre-commit-secrets.sh — scans STAGED content for leaked credentials before commit.
#
# Install into the current repo:
#   ./pre-commit-secrets.sh --install
#
# Ignore false positives, two ways:
#   1. Whole-file/glob:      add a line to .secretsignore at repo root, e.g.  tests/fixtures/*
#   2. Single line in code:  append `# pragma: allowlist secret` to that line
#
# Emergency bypass (use sparingly, it's logged by git anyway): git commit --no-verify

set -uo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

# ---- self-install -----------------------------------------------------
if [ "${1:-}" = "--install" ]; then
    REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
        echo -e "${RED}Not inside a git repo.${NC}"; exit 1;
    }
    HOOK_DIR="$REPO_ROOT/.git/hooks"
    mkdir -p "$HOOK_DIR"
    SELF_PATH=$(readlink -f "$0" 2>/dev/null || python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$0")
    cp "$SELF_PATH" "$HOOK_DIR/pre-commit"
    chmod +x "$HOOK_DIR/pre-commit"
    echo -e "${GREEN}✅ Installed to $HOOK_DIR/pre-commit${NC}"
    [ -f "$REPO_ROOT/.secretsignore" ] || cat > "$REPO_ROOT/.secretsignore" <<'EOF'
# One glob pattern per line, matched against the path relative to repo root.
# Example:
# tests/fixtures/*
# vendor/**
EOF
    exit 0
fi

# ---- locate repo --------------------------------------------------------
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo -e "${RED}❌ Not inside a git repository.${NC}"; exit 1;
}
cd "$REPO_ROOT" || exit 1

echo -e "${CYAN}🔒 Pre-Commit Secret Scanner${NC}"

# NUL-delimited so filenames with spaces/unicode/newlines survive intact.
STAGED_FILES=()
while IFS= read -r -d '' f; do
    STAGED_FILES+=("$f")
done < <(git diff --cached --name-only --diff-filter=ACM -z)

if [ "${#STAGED_FILES[@]}" -eq 0 ]; then
    echo -e "${GREEN}✅ No staged files.${NC}"
    exit 0
fi

SCANNER_PY="$(mktemp)"
trap 'rm -f "$SCANNER_PY"' EXIT

cat > "$SCANNER_PY" <<'PYEOF'
import sys, re, math, os, fnmatch, subprocess

RED, GREEN, YELLOW, NC = '\033[0;31m', '\033[0;32m', '\033[1;33m', '\033[0m'

# (name, regex) — order matters only for readability of output.
PATTERNS = [
    ("OpenAI key",            r"sk-[A-Za-z0-9]{20,}"),
    ("OpenAI project key",    r"sk-proj-[A-Za-z0-9_-]{20,}"),
    ("Anthropic key",         r"sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{20,}"),
    ("Groq key",              r"gsk_[A-Za-z0-9]{20,}"),
    ("Google/Firebase key",   r"AIza[0-9A-Za-z_-]{35}"),
    ("GitHub PAT (classic)",  r"ghp_[A-Za-z0-9]{36}"),
    ("GitHub OAuth token",    r"gho_[A-Za-z0-9]{36}"),
    ("GitHub fine-grained PAT", r"github_pat_[0-9A-Za-z_]{20,}"),
    ("AWS access key ID",     r"AKIA[0-9A-Z]{16}"),
    ("AWS secret key",        r"(?i)aws_secret_access_key\s*[:=]\s*['\"][A-Za-z0-9/+=]{40}['\"]"),
    ("Amazon MWS auth token", r"amzn\.mws\.[0-9a-f]{8}-[0-9a-f]{4}"),
    ("Slack token",           r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    ("Slack webhook",         r"hooks\.slack\.com/services/T[0-9A-Za-z]+/B[0-9A-Za-z]+/[0-9A-Za-z]+"),
    ("Stripe live key",       r"[sr]k_live_[0-9A-Za-z]{16,}"),
    ("SendGrid key",          r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"),
    ("Twilio API key SID",    r"SK[0-9a-fA-F]{32}"),
    ("Mailgun key",           r"key-[0-9a-f]{32}"),
    ("npm token",             r"npm_[A-Za-z0-9]{36}"),
    ("Discord bot token",     r"[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}"),
    ("Telegram bot token",    r"\d{8,10}:[A-Za-z0-9_-]{35}"),
    ("Private key block",     r"-----BEGIN (RSA|OPENSSH|EC|DSA|PGP) PRIVATE KEY-----"),
    ("Generic private key",   r"-----BEGIN PRIVATE KEY-----"),
    ("JWT",                   r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}"),
    ("DB conn string w/ pwd", r"(?i)(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^:\s'\"]+:[^@\s'\"]+@"),
    ("Keyword-style credential", r"(?i)(api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{12,}['\"]"),
]
COMPILED = [(name, re.compile(pat)) for name, pat in PATTERNS]

# Files we don't bother scanning: generated/vendored/binary-ish by convention.
SKIP_SUFFIXES = (
    ".lock", ".min.js", ".min.css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".whl", ".pyc",
)
SKIP_BASENAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Pipfile.lock", "poetry.lock"}
MAX_BYTES = 2 * 1024 * 1024  # 2MB — bigger files are almost certainly not hand-pasted secrets
SUPPRESS_MARKERS = ("pragma: allowlist secret", "nosecret")

def shannon(s):
    if not s:
        return 0.0
    from collections import Counter
    counts = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())

def load_ignore(path):
    patterns = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    return patterns

def is_ignored(relpath, ignore_patterns):
    return any(fnmatch.fnmatch(relpath, pat) for pat in ignore_patterns)

def get_staged_blob(relpath):
    """Content as it will actually be committed — not whatever's sitting in the
    working tree, which may differ from the index."""
    try:
        result = subprocess.run(
            ["git", "show", f":{relpath}"],
            capture_output=True, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return None

def redact(token):
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]} ({len(token)} chars)"

def scan_file(relpath, ignore_patterns):
    base = os.path.basename(relpath)
    if base in SKIP_BASENAMES or relpath.endswith(SKIP_SUFFIXES):
        return "skipped", []
    if is_ignored(relpath, ignore_patterns):
        return "skipped", []

    raw = get_staged_blob(relpath)
    if raw is None:
        return "skipped", []  # e.g. deleted/renamed edge cases
    if len(raw) > MAX_BYTES:
        return "skipped", []
    if b"\x00" in raw:
        return "skipped", []  # binary

    text = raw.decode("utf-8", errors="replace")
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(marker in line for marker in SUPPRESS_MARKERS):
            continue
        matched_here = False
        for name, regex in COMPILED:
            m = regex.search(line)
            if m:
                findings.append((i, name, redact(m.group(0))))
                matched_here = True
        if matched_here:
            continue
        # Fallback: bare high-entropy quoted tokens the named patterns missed.
        for tok in re.findall(r"""["']([A-Za-z0-9_~/+\-]{24,})["']""", line):
            if shannon(tok) > 4.5:
                findings.append((i, "High-entropy string", redact(tok)))
                break
    return "scanned", findings

def main():
    repo_root = os.getcwd()
    ignore_patterns = load_ignore(os.path.join(repo_root, ".secretsignore"))
    files = [p for p in sys.stdin.buffer.read().split(b"\0") if p]
    files = [p.decode("utf-8", errors="replace") for p in files]

    scanned = skipped = 0
    all_findings = []  # (relpath, line, name, redacted)
    for relpath in files:
        status, findings = scan_file(relpath, ignore_patterns)
        if status == "skipped":
            skipped += 1
        else:
            scanned += 1
            for line_no, name, red in findings:
                all_findings.append((relpath, line_no, name, red))

    print(f"   scanned {scanned} file(s), skipped {skipped} (binary/lock/ignored/oversized)")

    if all_findings:
        print(f"{RED}🚨 Possible secrets found:{NC}")
        for relpath, line_no, name, red in all_findings:
            print(f"  {RED}❌ [{relpath}:{line_no}]{NC} {name}: {red}")
        print(f"{YELLOW}If any of these are false positives:{NC}")
        print(f"{YELLOW}  - add the path/glob to .secretsignore, or{NC}")
        print(f"{YELLOW}  - append '# pragma: allowlist secret' to that line{NC}")
        sys.exit(1)

    print(f"{GREEN}✅ No secrets found. Safe to commit.{NC}")
    sys.exit(0)

if __name__ == "__main__":
    main()
PYEOF

printf '%s\0' "${STAGED_FILES[@]}" | python3 "$SCANNER_PY"
exit $?
