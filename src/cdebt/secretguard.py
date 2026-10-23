"""Keep secrets out of the sandbox and out of quiz records.

Two layers, both conservative (a false alarm only costs one question, a miss could leak a secret):

1. `is_secret_file`: files that typically hold credentials are never copied into the sandbox, even
   when git would include them (an `.env` someone forgot to list in `.gitignore`, a committed key).
2. `looks_secret` / `drop_secret_probes`: a question whose answer, options or text look like a
   secret is dropped. Answers are stored in `.cdebt/understanding.json` and in `refs/notes/cdebt`,
   which the pre-push hook pushes to the remote, so a secret must never become an answer.

This is pattern matching, not a guarantee: an unusual secret format can slip through. It lowers
the risk; it does not replace keeping secrets out of the repository.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import PurePosixPath

from cdebt.models import Probe

_SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker", ".gcloud"}
_SECRET_NAMES = {
    ".netrc",
    "_netrc",
    ".pypirc",
    ".npmrc",
    ".git-credentials",
    ".htpasswd",
    ".pgpass",
    ".my.cnf",
    "credentials",
    "credentials.json",
    "client_secret.json",
    "terraform.tfstate",
    "terraform.tfstate.backup",
}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk", ".kdbx", ".tfvars"}
_KEY_FILE_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
_SECRET_STEM = re.compile(
    r"^(secrets?|.*[-_.]secrets?|service[-_]?account.*|.*[-_]credentials?)"
    r"\.(json|ya?ml|toml|ini|cfg|conf|txt|env|properties)$"
)


def is_secret_file(rel: str) -> bool:
    """True for paths that typically hold credentials (never copied into the sandbox)."""
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if not parts:
        return False
    if any(p.lower() in _SECRET_DIRS for p in parts[:-1]):
        return True
    name = parts[-1].lower()
    if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
        return True
    if name in _SECRET_NAMES or name.startswith(_KEY_FILE_PREFIXES):
        return True
    if PurePosixPath(name).suffix in _SECRET_SUFFIXES:
        return True
    return bool(_SECRET_STEM.match(name))


_TOKEN_PATTERNS = [
    re.compile(p)
    for p in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",  # AWS access key id
        r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",  # GitHub tokens
        r"\bgithub_pat_[A-Za-z0-9_]{30,}\b",
        r"\bglpat-[A-Za-z0-9_-]{20,}\b",  # GitLab
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",  # Slack
        r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b",  # OpenAI / Anthropic style
        r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b",  # Stripe secret / restricted keys
        r"\bAIza[0-9A-Za-z_-]{35}\b",  # Google API key
        r"\bnpm_[A-Za-z0-9]{30,}\b",
        r"\bpypi-AgE[A-Za-z0-9_-]{20,}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWT
        r"hooks\.slack\.com/services/",
        r"[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:[^\s/@'\"]+@",  # credentials inside a URL
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|"
        r"private[_-]?key|client[_-]?secret)['\"]?\s*[:=]\s*['\"][^'\"\s]{6,}",
    )
]
_CANDIDATE = re.compile(r"[A-Za-z0-9+/_=-]{24,}")


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def looks_secret(text: str) -> bool:
    """Known credential formats, or a long random-looking token with upper case, lower case and
    digits. Hex digests and UUIDs (no upper case, low entropy) are not flagged."""
    if not text:
        return False
    if any(p.search(text) for p in _TOKEN_PATTERNS):
        return True
    for tok in _CANDIDATE.findall(text):
        classes = sum(
            (
                any(c.islower() for c in tok),
                any(c.isupper() for c in tok),
                any(c.isdigit() for c in tok),
            )
        )
        if classes == 3 and _entropy(tok) >= 4.3:
            return True
    return False


def drop_secret_probes(probes: list[Probe]) -> tuple[list[Probe], int]:
    """Remove questions whose answer, options or text look like a secret."""
    kept: list[Probe] = []
    dropped = 0
    for p in probes:
        texts = [p.answer, p.question, *p.options]
        if any(looks_secret(t) for t in texts):
            dropped += 1
            continue
        kept.append(p)
    return kept, dropped
