#!/usr/bin/env python3
"""Deterministic PR gate. Stdlib only.

Usage: gate.py <check> [base_sha]
checks: type | onboarding | hexagonal | coverage | observability | content
Config: .github/pr-standards.json (all keys optional), e.g.
  {"type": "code|deployable|pages", "domain": ["src/domain"], "application": ["src/application"],
   "coverage_min": 80, "coverage_file": "coverage.xml"}
"""
import json, os, re, subprocess, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SRC_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".cs", ".java", ".kt", ".rb", ".ps1", ".psm1", ".gs", ".ipynb", ".m", ".c", ".cpp", ".h", ".swift", ".rs", ".php", ".scala"}
CONTENT_EXT = {".md", ".markdown", ".html", ".htm", ".txt"}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "bin", "obj", ".venv", "venv", "__pycache__", "_site", ".next"}
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|testing)(/|$)|(_test\.go|\.test\.[jt]sx?|\.spec\.[jt]sx?|Tests?\.cs|(^|/)test_[^/]+\.py|_test\.py)$", re.I)
DOMAIN_SEG = {"domain"}
APP_SEG = {"application", "usecases", "use_cases", "use-cases"}
ADAPTER_SEG = {"adapters", "adapter", "infrastructure", "infra", "controllers", "api", "web", "persistence", "repositories"}
# Third-party IO/framework modules forbidden in domain & application (ports must be interfaces).
FRAMEWORKS = re.compile(
    r"^(flask|django|fastapi|starlette|requests|httpx|aiohttp|sqlalchemy|psycopg2?|pymongo|redis|boto3|botocore|"
    r"express|koa|@nestjs|react|next|axios|pg|mongoose|prisma|@prisma|typeorm|sequelize|@aws-sdk|"
    r"net/http|database/sql|github\.com/gin-gonic|github\.com/labstack|gorm\.io|"
    r"Microsoft\.AspNetCore|Microsoft\.EntityFrameworkCore|System\.Data|System\.Net\.Http|Dapper)(\b|/|\.|$)")
DEPLOY_MARKERS = re.compile(r"(^|/)(Dockerfile|docker-compose\.ya?ml|compose\.ya?ml|Procfile|fly\.toml|app\.ya?ml|serverless\.ya?ml|"
                            r"vercel\.json|netlify\.toml|azure-pipelines\.yml|[^/]+\.tf|Chart\.yaml|kustomization\.ya?ml|playbook[^/]*\.ya?ml)$")
OBS_FILES = re.compile(r"(^|/)(alerts?|alerting|monitoring|observability|dashboards?)(/|[^/]*\.(ya?ml|json|tf|md)$)|"
                       r"(^|/)[^/]*(alarm|alert|monitor)[^/]*\.(tf|ya?ml|json|bicep)$", re.I)
LOG_RE = re.compile(r"\b(logging\.getLogger|structlog|loguru|ILogger|Serilog|NLog|log/slog|zap\.|zerolog|logrus|winston|pino|bunyan|console\.error|Write-Log|opentelemetry|OpenTelemetry|applicationinsights|sentry)", re.I)

failures = []


def fail(msg, file=None, line=None):
    """Annotate (shows on the PR's Files tab) and collect for the job summary."""
    failures.append((msg, file, line))
    loc = f" file={file}" + (f",line={line}" if line else "") if file else ""
    print(f"::error{loc}::{msg}")


def summarise(check):
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if not out:
        return
    with open(out, "a") as fh:
        fh.write(f"### {check}: {'❌ ' + str(len(failures)) + ' issue(s)' if failures else '✅ passed'}\n\n")
        for msg, file, line in failures[:200]:
            fh.write(f"- {'`' + file + (':' + str(line) if line else '') + '` ' if file else ''}{msg}\n")
        if HELP.get(check) and failures:
            fh.write(f"\n{HELP[check]}\n")


HELP = {
    "content": "Accept a word by adding it to `.github/wordlist.txt` (one per line). "
               "Limit which files are checked with `content_paths` / `content_exclude` in `.github/pr-standards.json`.",
    "hexagonal": "Layer folders can be mapped with `domain` / `application` lists in `.github/pr-standards.json`.",
    "coverage": "Only domain and application code is measured. Expose `make coverage` (or `npm run coverage`) writing Cobertura `coverage.xml`.",
}


def cfg():
    p = Path(".github/pr-standards.json")
    return json.loads(p.read_text()) if p.exists() else {}


def files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split("\n")
    return [f for f in out if f and not (set(f.split("/")) & SKIP_DIRS)]


def changed(base):
    if not base:
        return files()
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD"], capture_output=True, text=True).stdout
    return [f for f in out.split("\n") if f and Path(f).exists()]


def source(fs):
    return [f for f in fs if Path(f).suffix in SRC_EXT and not TEST_RE.search(f)]


def layer(path, c):
    for key, segs in (("domain", DOMAIN_SEG), ("application", APP_SEG)):
        if any(path.startswith(p.rstrip("/") + "/") for p in c.get(key, [])):
            return key
        if set(path.lower().split("/")[:-1]) & segs:
            return key
    if set(path.lower().split("/")[:-1]) & ADAPTER_SEG:
        return "adapter"
    return "other"


def repo_type(c, fs=None):
    if c.get("type"):
        return c["type"]
    fs = files() if fs is None else fs
    src = source(fs)
    if any(DEPLOY_MARKERS.search(f) for f in fs) or any(Path(f).name.startswith("deploy") for f in fs if f.startswith(".github/workflows/")):
        return "deployable" if src or any(f.endswith(".tf") for f in fs) else "pages"
    if len(src) == 0 or ("_config.yml" in fs and len(src) < 5):
        return "pages"
    return "code"


def check_onboarding(_):
    readme = next((p for p in ("README.md", "readme.md", "README.rst", "README") if Path(p).exists()), None)
    if not readme or len(Path(readme).read_text(errors="ignore").split()) < 50:
        fail("README missing or under 50 words: describe purpose, prerequisites, build, run and test.")
    mk = Path("Makefile").read_text(errors="ignore") if Path("Makefile").exists() else ""
    targets = set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", mk, re.M))
    scripts = json.loads(Path("package.json").read_text()).get("scripts", {}) if Path("package.json").exists() else {}
    ok_build = (mk and (targets - {".PHONY"})) or "build" in scripts
    ok_run = "run" in targets or "serve" in scripts
    if not ok_build:
        fail("No build entry point: add a Makefile (default target builds) or package.json script 'build'.")
    if not ok_run:
        fail("No run entry point: add Makefile target 'run' or package.json script 'serve'.")


def imports(path):
    text = Path(path).read_text(errors="ignore")
    ext = Path(path).suffix
    if ext == ".py":
        return re.findall(r"^\s*(?:from|import)\s+([\w.]+)", text, re.M)
    if ext in {".js", ".jsx", ".ts", ".tsx"}:
        return [a or b for a, b in re.findall(r"""from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]""", text)]
    if ext == ".go":
        blocks = re.findall(r"import\s*\(([^)]*)\)", text, re.S) + re.findall(r'import\s+(?:\w+\s+)?("[^"]+")', text)
        return re.findall(r'"([^"]+)"', "\n".join(blocks))
    if ext == ".cs":
        return re.findall(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", text, re.M)
    return []


def check_hexagonal(base):
    c = cfg()
    src = source(files())
    if not src:
        return
    layers = {f: layer(f, c) for f in src}
    if "domain" not in layers.values():
        fail("No domain layer found (a 'domain/' directory or 'domain' paths in .github/pr-standards.json). Business rules belong in domain/application.")
    if "application" not in layers.values():
        fail("No application layer found ('application/' or 'usecases/' directory). Use cases belong there.")
    for f in source(changed(base)):
        lay = layers.get(f) or layer(f, c)
        if lay not in ("domain", "application"):
            continue
        src_lines = Path(f).read_text(errors="ignore").split("\n")
        for imp in imports(f):
            ln = next((n for n, l in enumerate(src_lines, 1) if imp in l), None)
            segs = set(re.split(r"[./\\]", imp.lower()))
            if segs & ADAPTER_SEG or (lay == "domain" and segs & APP_SEG):
                fail(f"{lay} layer imports '{imp}' — dependencies must point inward (adapters -> application -> domain).", f, ln)
            elif FRAMEWORKS.search(imp):
                fail(f"{lay} layer imports framework/IO module '{imp}' — depend on a port interface instead.", f, ln)


def check_coverage(_):
    c = cfg()
    src = [f for f in source(files()) if layer(f, c) in ("domain", "application")]
    if not src:
        return  # absence of layers is reported by the hexagonal check
    mk = Path("Makefile").read_text(errors="ignore") if Path("Makefile").exists() else ""
    scripts = json.loads(Path("package.json").read_text()).get("scripts", {}) if Path("package.json").exists() else {}
    if re.search(r"^coverage\s*:", mk, re.M):
        cmd = ["make", "coverage"]
    elif "coverage" in scripts:
        cmd = ["npm", "run", "coverage"]
    else:
        return fail("No 'make coverage' target or npm 'coverage' script producing Cobertura XML (coverage.xml).")
    if subprocess.run(cmd).returncode != 0:
        return fail(f"'{' '.join(cmd)}' failed — tests must pass.")
    report = Path(c.get("coverage_file", "coverage.xml"))
    if not report.exists():
        hits = list(Path(".").rglob("coverage.cobertura.xml")) + list(Path(".").rglob("cobertura-coverage.xml"))
        if not hits:
            return fail(f"Coverage report {report} not found (Cobertura XML expected).")
        report = hits[0]
    root = ET.parse(report).getroot()
    sources = [s.text or "" for s in root.iter("source")]
    covered = total = 0
    for cls in root.iter("class"):
        fn = cls.get("filename", "").replace("\\", "/")
        cands = [fn] + [os.path.relpath(os.path.join(s, fn)) for s in sources if s]
        if not any(layer(p, c) in ("domain", "application") for p in cands):
            continue
        for line in cls.iter("line"):
            total += 1
            covered += int(line.get("hits", "0")) > 0
    minimum = c.get("coverage_min", 80)
    pct = 100.0 * covered / total if total else 0.0
    print(f"domain+application line coverage: {pct:.1f}% ({covered}/{total}), minimum {minimum}%")
    if pct < minimum:
        fail(f"Domain+application coverage {pct:.1f}% is below {minimum}%. Test business logic (other layers are not measured).")


def check_observability(_):
    fs = files()
    if not any(OBS_FILES.search(f) for f in fs):
        fail("Deployable repo has no alerting/monitoring config (e.g. alerts/, monitoring/, *alarm*.tf) or docs/observability.md.")
    if not any(LOG_RE.search(Path(f).read_text(errors="ignore")) for f in source(fs)[:500]):
        fail("Deployable repo has no structured logging/telemetry usage detected.")


CONTENT_SKIP_NAMES = re.compile(r"^(README|CHANGELOG|LICEN[CS]E|CONTRIBUTING|CODE_OF_CONDUCT|SECURITY|CLAUDE|AGENTS|GEMINI|copilot-instructions)(\.|$)", re.I)
PUBLISHED_UNDERSCORE = {"_posts", "_drafts", "_pages"}
DEFAULT_CONTENT_EXCLUDE = ["docs/superpowers/", "docs/plans/", "docs/specs/", "docs/adr/", "scripts/", "vendor/"]
NOISY_RULES = "UPPERCASE_SENTENCE_START,ENGLISH_WORD_REPEAT_BEGINNING_RULE,DOUBLE_PUNCTUATION,COMMA_PARENTHESIS_WHITESPACE,WHITESPACE_RULE,EN_QUOTES,DASH_RULE"


def jekyll_excludes():
    p = Path("_config.yml")
    if not p.exists():
        return []
    m = re.search(r"^exclude:\s*(\[[^\]]*\]|(?:\n\s*-\s*.+)+)", p.read_text(errors="ignore"), re.M)
    return [x.strip(" '\"[]-") for x in re.split(r"[,\n]", m.group(1)) if x.strip(" '\"[]-")] if m else []


def is_content(f, c):
    """Published prose only: what Jekyll/Pages would render, minus repo/tooling docs."""
    parts = f.split("/")
    if Path(f).suffix.lower() not in CONTENT_EXT or CONTENT_SKIP_NAMES.match(parts[-1]):
        return False
    if any(p.startswith(".") or (p.startswith("_") and p not in PUBLISHED_UNDERSCORE) for p in parts[:-1]) or set(parts) & SKIP_DIRS:
        return False
    if c.get("content_paths"):
        return any(f.startswith(p.rstrip("/") + "/") or f == p for p in c["content_paths"])
    excl = c.get("content_exclude", DEFAULT_CONTENT_EXCLUDE) + jekyll_excludes()
    return not any(f == e.rstrip("/") or f.startswith(e.rstrip("/") + "/") for e in excl)


def strip_markup(text, ext):
    text = re.sub(r"```.*?```|^---\n.*?\n---\n|\{%.*?%\}|\{\{.*?\}\}", " ", text, flags=re.S | re.M)
    text = re.sub(r"`[^`\n]*`", "code", text)  # keep sentences grammatical where inline code was
    if ext in {".html", ".htm"}:
        text = re.sub(r"<(script|style|code|pre)\b.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)|https?://\S+", r"\1", text)
    return re.sub(r"[ \t]+", " ", text)


def check_content(base):
    lt = os.environ.get("LT_URL", "http://localhost:8010/v2/check")
    words = Path(".github/wordlist.txt")
    allow = set(words.read_text().split()) if words.exists() else set()
    c = cfg()
    lang = c.get("language", "en-GB")
    for f in changed(base):
        if not is_content(f, c):
            continue
        ext = Path(f).suffix.lower()
        raw = Path(f).read_text(errors="ignore")
        text = strip_markup(raw, ext)
        for i in range(0, len(text), 15000):
            chunk = text[i:i + 15000]
            data = urllib.parse.urlencode({"text": chunk, "language": lang, "disabledCategories": "TYPOGRAPHY,STYLE,REDUNDANCY", "disabledRules": NOISY_RULES}).encode()
            res = json.load(urllib.request.urlopen(lt, data, timeout=120))
            for m in res["matches"]:
                bad = chunk[m["offset"]:m["offset"] + m["length"]]
                if bad in allow:
                    continue
                fix = ", ".join(r["value"] for r in m["replacements"][:3])
                line = next((n for n, l in enumerate(raw.split("\n"), 1) if bad and bad in l), None)
                fail(f"'{bad}': {m['message']}" + (f" (try: {fix})" if fix else ""), f, line)


if __name__ == "__main__":
    check, base = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    if check == "summary":  # used by the final gate job
        res = json.loads(os.environ["NEEDS"])
        bad = [k for k, v in res.items() if v["result"] not in ("success", "skipped")]
        lines = ["## pr-standards", "", "| Check | Result |", "|---|---|"] + [
            f"| {k} | {'✅' if v['result'] == 'success' else '⏭️ n/a' if v['result'] == 'skipped' else '❌ ' + v['result']} |" for k, v in res.items()]
        if bad:
            lines += ["", f"Blocked by: **{', '.join(bad)}**. Open the failing job above for the itemised reasons."]
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
        for k in bad:
            print(f"::error::pr-standards blocked by '{k}' — see that job's summary for details.")
        sys.exit(1 if bad else 0)
    if check == "type":
        print(repo_type(cfg()))
        sys.exit(0)
    globals()[f"check_{check}"](base)
    summarise(check)
    print(f"{check}: {'FAILED' if failures else 'passed'} ({len(failures)} issue(s))")
    sys.exit(1 if failures else 0)
