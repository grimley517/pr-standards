#!/usr/bin/env python3
"""Apply pr-standards to every owned, non-fork, non-archived repo. Idempotent; uses the gh CLI.

  rollout.py [--dry-run] [--enforce] [--owners grimley517,grimpop] [repo ...]

Per repo: one commit adding only the caller workflow (repo type detected once and pinned as its
`type:` input; edit it to override) and .github/copilot-instructions.md, then upserts the "pr-standards" ruleset: automatic Copilot review, plus (with --enforce)
PR required and "pr-standards / gate" as a required check. Without --enforce nothing blocks merging.
"""
import base64, json, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gate import repo_type  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
CHECK = "pr-standards / gate"
MARK = "## Repo notes"


def gh(*args, body=None, ok404=False):
    cmd = ["gh", "api", *args] + (["--input", "-"] if body is not None else [])
    for attempt in range(4):  # retry transient/secondary-rate-limit failures
        r = subprocess.run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
        if not r.returncode or "404" in r.stdout + r.stderr or "422" in r.stdout + r.stderr:
            break
        time.sleep(5 * (attempt + 1))
    if r.returncode:
        if ok404 and "404" in (r.stdout + r.stderr):
            return None
        raise RuntimeError(f"gh api {' '.join(args)}: {(r.stdout + r.stderr).strip()[:300]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def repos(owners):
    me = gh("user")["login"]
    for o in owners:
        url = "user/repos?affiliation=owner&per_page=100" if o == me else f"orgs/{o}/repos?per_page=100"
        for r in json.loads(subprocess.run(["gh", "api", "--paginate", url, "--jq", "[.[]]"], capture_output=True, text=True, check=True).stdout.replace("]\n[", ",")):
            if r["owner"]["login"].lower() == o.lower() and not r["archived"] and not r["fork"] and r["name"] != "pr-standards":
                yield r


def text(repo, path, ref):
    f = gh(f"repos/{repo}/contents/{path}?ref={ref}", ok404=True)
    return base64.b64decode(f["content"]).decode() if f and "content" in f else None


def desired(r, tree):
    repo, ref = r["full_name"], r["default_branch"]
    caller = text(repo, ".github/workflows/pr-standards.yml", ref) or ""
    pinned = caller.split("type:", 1)[1].split()[0] if "\n      type:" in caller else ""  # keep manual overrides
    cfg = {"type": pinned} if pinned in ("code", "deployable", "pages") else {}
    if not cfg.get("type"):
        is_pages = r["has_pages"] or r["name"].lower().endswith(".github.io")
        t = repo_type({}, tree)
        cfg["type"] = "pages" if is_pages and t != "deployable" and len([f for f in tree if f.endswith((".md", ".html"))]) else t
    kind = cfg["type"]
    body = (T / "copilot" / ("pages.md" if kind == "pages" else "code.md")).read_text()
    if kind == "deployable":
        body += (T / "copilot" / "deployable.md").read_text()
    old = text(repo, ".github/copilot-instructions.md", ref) or ""
    notes = old.split(MARK, 1)[1].strip() if MARK in old else old.strip()  # keep pre-existing instructions
    files = {
        ".github/workflows/pr-standards.yml": (T / "pr-standards.yml").read_text().replace("__TYPE__", kind),
        ".github/copilot-instructions.md": f"{body}\n{MARK}\n{notes}\n".rstrip() + "\n",
    }
    changed = {p: c for p, c in files.items() if text(repo, p, ref) != c}
    return kind, changed


def commit(r, head_sha, files):
    repo = r["full_name"]
    base_tree = gh(f"repos/{repo}/git/commits/{head_sha}")["tree"]["sha"]
    tree = gh(f"repos/{repo}/git/trees", "-X", "POST", body={"base_tree": base_tree, "tree": [
        {"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()]})
    c = gh(f"repos/{repo}/git/commits", "-X", "POST", body={
        "message": "chore: apply pr-standards gate and Copilot review instructions\n\nManaged by grimley517/pr-standards.",
        "tree": tree["sha"], "parents": [head_sha]})
    gh(f"repos/{repo}/git/refs/heads/{r['default_branch']}", "-X", "PATCH", body={"sha": c["sha"]})


def ruleset(r, enforce):
    repo = r["full_name"]
    body = {"name": "pr-standards", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": ([
                {"type": "pull_request", "parameters": {"required_approving_review_count": 0, "dismiss_stale_reviews_on_push": False,
                                                        "require_code_owner_review": False, "require_last_push_approval": False,
                                                        "required_review_thread_resolution": False}},
                {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": False,
                                                                  "required_status_checks": [{"context": CHECK}]}},
            ] if enforce else []) + [
                {"type": "copilot_code_review", "parameters": {"review_on_push": True, "review_draft_pull_requests": False}},
            ]}
    existing = next((x for x in gh(f"repos/{repo}/rulesets") or [] if x["name"] == "pr-standards"), None)
    if existing:
        gh(f"repos/{repo}/rulesets/{existing['id']}", "-X", "PUT", body=body)
    else:
        gh(f"repos/{repo}/rulesets", "-X", "POST", body=body)


def main(argv):
    dry = "--dry-run" in argv
    enforce = "--enforce" in argv  # also set `enforce: true` default in gate.yml
    owners = ["grimley517", "grimpop"]
    if "--owners" in argv:
        owners = argv[argv.index("--owners") + 1].split(",")
    only = {a for a in argv if not a.startswith("--") and a not in ",".join(owners).split(",")}
    rc = 0
    for r in repos(owners):
        if only and r["name"] not in only and r["full_name"] not in only:
            continue
        name = r["full_name"]
        try:
            if r["size"] == 0 or not gh(f"repos/{name}/branches/{r['default_branch']}", ok404=True):
                print(f"SKIP  {name}: empty")
                continue
            head = gh(f"repos/{name}/branches/{r['default_branch']}")["commit"]["sha"]
            tree = [e["path"] for e in gh(f"repos/{name}/git/trees/{head}?recursive=1")["tree"] if e["type"] == "blob"]
            kind, files = desired(r, tree)
            print(f"{'PLAN' if dry else 'APPLY'} {name} [{kind}] files:{sorted(files) or 'up-to-date'}")
            if dry:
                continue
            if files:
                commit(r, head, files)
            ruleset(r, enforce)
        except Exception as e:  # keep going; report at end
            rc = 1
            print(f"FAIL  {name}: {e}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
