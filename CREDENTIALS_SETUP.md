# Moving Database Credentials out of the Script

These instructions describe a small, portable pattern for keeping database passwords (or any other secret) out of your Python scripts. The pattern uses an environment variable that can optionally be loaded from a local `.env` file sitting next to the script. It has no third-party dependencies.

Apply the same pattern anywhere a script currently has a hard-coded password, API key, or other secret.

## Why

When a password is written directly into a `.py` file, every copy of that file — in email, in a shared folder, in a git repo, in a backup — contains the live credential. Moving the secret to a `.env` file (which is never committed) means the script itself is safe to share, diff, and archive. If the credential ever leaks or needs to rotate, only the `.env` file changes.

## What to add to the script

1. At the top of the script, near the other imports, add a tiny `.env` loader. It does not require the `python-dotenv` library.

```python
import os
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path):
    """Minimal .env loader — KEY=VALUE lines, no dependencies.

    Lines starting with '#' are ignored. Existing environment variables
    take precedence (so real env vars always win over the file)."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(os.path.join(SCRIPT_DIR, ".env"))
```

2. Replace any hard-coded secret with a call to `os.environ.get(...)`. Keep non-sensitive defaults (like a hostname) inline so the script still runs with minimal setup.

```python
DB_HOST     = os.environ.get("DB_HOST",  "your-default-host")
DB_PORT     = int(os.environ.get("DB_PORT", "3306"))
DB_USER     = os.environ.get("DB_USER",  "your-default-user")
DB_NAME     = os.environ.get("DB_NAME",  "your-default-db")
DB_PASSWORD = os.environ.get("DB_PASSWORD")   # no default — must be set
```

3. Fail fast with a helpful message if the secret is missing, so the script never silently tries to connect with an empty password.

```python
if not DB_PASSWORD:
    print("DB_PASSWORD is not set.")
    print("  Export it in your shell:  export DB_PASSWORD='your-password'")
    print("  Or create a `.env` file next to this script containing:")
    print("      DB_PASSWORD=your-password")
    sys.exit(1)
```

## What to create alongside the script

Create two files in the same folder as the script.

`.env` — the real file with the actual password. Never commit this or share it in cleartext.

```
DB_PASSWORD=the-real-password
```

`.env.example` — a safe-to-share template that documents which variables are needed. Commit this one.

```
DB_PASSWORD=your-password-here
```

## `.gitignore`

Add `.env` (but not `.env.example`) to the project's `.gitignore` so git never tracks the real credentials:

```
.env
```

## Rotating the leaked password

Because the old password `your-password-here` was sitting in a plain `.py` file, treat it as exposed and rotate it when convenient — through cPanel on TigerTech, under **MySQL Databases → Current Users → Change Password**. After changing it, update the `.env` file on each machine that runs the script. No code changes are needed.

## Precedence, in one sentence

A real environment variable (set via `export DB_PASSWORD=...` or a system-level secret manager) always wins; the `.env` file only fills in values that haven't already been set. That lets the same script work locally (via `.env`) and in a server or CI environment (via a real env var) without branching logic.
