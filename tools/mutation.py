#!/usr/bin/env python3
"""Measures how many small changes to hashicorp/hcl the tests catch (mutation testing).

    python3 tools/mutation.py run [<package>]          # run every mutant of a package (default hclsyntax)
    python3 tools/mutation.py report [<package>]       # summarize the last run
    python3 tools/mutation.py survivors [<package>]    # list survivors that coverage/mutation/ doesn't classify
    python3 tools/mutation.py try <package> <mutant> <op> <input> [<context>]
                                                       # run hashicorp/hcl and one mutant on an input
    python3 tools/mutation.py classify <package> <records.jsonl>
                                                       # add classified survivors to coverage/mutation/

A mutant is hashicorp/hcl with one small change to the package's hand-written
code: an operator swapped, a condition forced, a statement removed
(tools/mutate/main.go lists them). For each mutant, "run" builds the Go adapter
with the change and runs the tests that execute the changed line, stopping at
the first one that fails. A mutant survives when they all pass. A survivor is
either a gap in the tests or a change the protocol can't see, such as an error
message, a source range or code no input can reach. coverage/mutation/<package>.json
records which survivors are which, with reasons, and "report" scores the tests
on the mutants the protocol can see.

<package> is a package of hashicorp/hcl: hclsyntax, json, ext/dynblock and so
on, or . for the root package. Needs Go. Work files go in a directory under
the system's temporary directory (--work to change it); a run resumes where
it stopped unless the tests or the mutants changed.
"""

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runner"))
import hcltest  # noqa: E402  reuses the runner's test loading and comparison

ADAPTER_DIR = ROOT / "adapters" / "go"
HCL = "github.com/hashicorp/hcl/v2"
# Packages whose per-test coverage is recorded. Go only writes coverage data if
# the main package is instrumented too.
COVER_PACKAGES = ["", "hclsyntax", "json", "ext/customdecode", "ext/dynblock", "ext/tryfunc", "ext/typeexpr",
                  "ext/userfunc"]
CLASSES = {
    "diagnostics": "can only change error messages, the number or order of errors, or their ranges",
    "ranges": "can only change source ranges of syntax nodes or tokens",
    "marks": "can only change cty value marks",
    "normalized": "only changes output that the protocol defines as equivalent",
    "two_mistakes": "only an input with two mistakes tells it apart, and each error test has one mistake",
    "equivalent": "no input changes any behavior",
    "unreachable": "only differs in states the protocol can't produce, such as Go API uses",
    "gap": "a test could catch it, and none does yet",
}
INVISIBLE = set(CLASSES) - {"gap"}
TIMEOUT = 10


def main():
    parser = argparse.ArgumentParser(description="Mutation testing of hashicorp/hcl against the suite.")
    parser.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "hcl-test-suite-mutation",
                        help="directory for work files")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="run every mutant of a package")
    p.add_argument("package", nargs="?", default="hclsyntax")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    p = sub.add_parser("report", help="summarize the last run")
    p.add_argument("package", nargs="?", default="hclsyntax")
    p.add_argument("--files", action="store_true", help="also break the results down by file")
    p = sub.add_parser("survivors", help="list survivors")
    p.add_argument("package", nargs="?", default="hclsyntax")
    p.add_argument("--all", action="store_true", help="also list classified survivors")
    p.add_argument("--json", action="store_true", help="print JSON, for triage")
    p = sub.add_parser("try", help="run hashicorp/hcl and one mutant on an input")
    p.add_argument("package")
    p.add_argument("mutant", help="a mutant's id or key")
    p.add_argument("op", choices=hcltest.OPERATIONS)
    p.add_argument("input", type=Path)
    p.add_argument("context", type=Path, nargs="?")
    p = sub.add_parser("classify", help="add classified survivors to coverage/mutation/")
    p.add_argument("package")
    p.add_argument("records", type=Path, help='JSON lines with "key" or "id", "class" and "reason"')
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    return {"run": cmd_run, "report": cmd_report, "survivors": cmd_survivors, "try": cmd_try,
            "classify": cmd_classify}[args.command](args)


# Setup

def go(args, cwd=ADAPTER_DIR, **kw):
    return subprocess.run(["go"] + args, cwd=cwd, check=True, capture_output=True, text=True, **kw).stdout


def setup(work):
    """Makes a writable copy of the hashicorp/hcl module the adapter uses, and a
    modfile that builds the adapter with it. Mutants are applied to the copy
    with go build -overlay, which can't replace files in the module cache."""
    info = json.loads(go(["list", "-m", "-json", HCL]))
    copy = work / f"hcl-{info['Version']}"
    if not (copy / "go.mod").exists():
        tmp = work / "hcl-copying"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(info["Dir"], tmp)
        for dirpath, dirnames, filenames in os.walk(tmp):
            for name in dirnames + filenames:
                path = os.path.join(dirpath, name)
                os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
        tmp.rename(copy)
    modfile = work / "adapter.mod"
    write_if_changed(modfile, (ADAPTER_DIR / "go.mod").read_bytes() + f"\nreplace {HCL} => {copy}\n".encode())
    write_if_changed(work / "adapter.sum", (ADAPTER_DIR / "go.sum").read_bytes())
    return {"copy": copy, "modfile": modfile, "version": info["Version"]}


def write_if_changed(path, data):
    """Writes a file atomically, and only if its contents change, since several
    processes may run setup at once."""
    if path.exists() and path.read_bytes() == data:
        return
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def build(env, out, overlay=None, cover=False):
    args = ["build", "-modfile=" + str(env["modfile"]), "-o", str(out)]
    if overlay:
        args += ["-overlay", str(overlay)]
    if cover:
        args += ["-cover", "-coverpkg=" + ",".join(["./..."] + [f"{HCL}/{p}".rstrip("/") for p in COVER_PACKAGES])]
    return subprocess.run(["go"] + args + ["."], cwd=ADAPTER_DIR, capture_output=True, text=True)


def fingerprint(paths):
    h = hashlib.sha256()
    for path in paths:
        h.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def tests_fingerprint():
    files = sorted(p for p in (ROOT / "tests").rglob("*") if p.is_file())
    return fingerprint(files + [ROOT / "runner" / "hcltest.py", ADAPTER_DIR / "main.go", ADAPTER_DIR / "go.mod"])


def read_cache(path, fp):
    """A cache file's data, or None if it is missing or was made for other tests."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("fingerprint") == fp else None


def import_path(package):
    """The import path of a package given as on the command line, where . is the root package."""
    return HCL if package == "." else f"{HCL}/{package}"


def package_dir(package):
    """The package's directory inside the module, as coverage profiles name it."""
    return "" if package == "." else package


def slug(package):
    """A file name for the package: hcl for the root package, ext-dynblock for ext/dynblock."""
    return "hcl" if package == "." else package.replace("/", "-")


def pkg_dir(work, package):
    d = work / slug(package)
    d.mkdir(parents=True, exist_ok=True)
    return d


# Running tests

def command(test, tmp):
    """The adapter arguments for a test, as hcltest.run_test builds them."""
    args = [test.op, str(test.input)]
    if test.context:
        f = tmp / (test.name.replace("/", "__") + ".json")
        f.write_text(json.dumps(test.context, ensure_ascii=False), encoding="utf-8")
        args.append(str(f))
    return args


def run_one(binary, test, tmp, env_extra=None):
    """Runs one test. Returns (outcome, message, output): outcome is "pass",
    "fail" or "error", and output is the adapter's parsed output, if any."""
    adapter = [str(binary)] if not env_extra else ["env"] + [f"{k}={v}" for k, v in env_extra.items()] + [str(binary)]
    try:
        actual = hcltest.call_adapter(adapter, command(test, tmp), TIMEOUT)
        failure = hcltest.compare(test, actual)
    except hcltest.AdapterError as e:
        return "error", str(e), None
    except RecursionError:
        return "error", "adapter output is nested too deeply", None
    return ("fail", failure[0], actual) if failure else ("pass", None, actual)


def base_outputs(work, env, tests, fp):
    """hashicorp/hcl's output for every test, from the unmutated copy."""
    cache = work / "base-outputs.json"
    data = read_cache(cache, fp)
    if data:
        return data["outputs"]
    binary = work / "base-adapter"
    result = build(env, binary)
    if result.returncode:
        sys.exit("building the adapter failed:\n" + result.stderr)
    tmp = work / "base-ctx"
    tmp.mkdir(exist_ok=True)
    outputs, bad = [], []
    for test in tests:
        outcome, message, out = run_one(binary, test, tmp)
        if outcome != "pass":
            bad.append(f"{test.name}: {message}")
        outputs.append(out)
    if bad:
        sys.exit("tests fail against unmutated hashicorp/hcl:\n" + "\n".join(bad[:20]))
    cache.write_text(json.dumps({"fingerprint": fp, "outputs": outputs}))
    return outputs


# Per-test coverage

def coverage_worker(job):
    binary, test, d = job
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "ctx"
    tmp.mkdir(exist_ok=True)
    outcome, message, _ = run_one(binary, test, tmp, {"GOCOVERDIR": str(d)})
    profile = d / "profile.txt"
    subprocess.run(["go", "tool", "covdata", "textfmt", "-i", str(d), "-o", str(profile)], check=True,
                   cwd=ADAPTER_DIR, capture_output=True)
    blocks = []
    for line in profile.read_text().splitlines()[1:]:
        loc, _n, count = line.rsplit(" ", 2)
        if count != "0" and loc.startswith(HCL + "/"):
            blocks.append(loc[len(HCL) + 1:])
    shutil.rmtree(d)
    return outcome, message, blocks


def per_test_coverage(work, env, tests, fp, workers):
    """For every block of the covered packages, the indexes of the tests that execute it."""
    cache = work / "coverage.json"
    data = read_cache(cache, fp)
    if data:
        return data["blocks"]
    print("recording which tests execute each line", flush=True)
    binary = work / "cover-adapter"
    result = build(env, binary, cover=True)
    if result.returncode:
        sys.exit("building the adapter failed:\n" + result.stderr)
    root = work / "cover"
    shutil.rmtree(root, ignore_errors=True)
    with mp.Pool(workers) as pool:
        results = pool.map(coverage_worker, [(binary, t, root / str(i)) for i, t in enumerate(tests)])
    bad = [f"{tests[i].name}: {m}" for i, (o, m, _) in enumerate(results) if o != "pass"]
    if bad:
        sys.exit("tests fail against the instrumented adapter:\n" + "\n".join(bad[:20]))
    # Every block, including ones no test executes, from a profile of an empty run.
    blocks = defaultdict(list)
    d = root / "all"
    d.mkdir(parents=True)
    subprocess.run(["env", f"GOCOVERDIR={d}", str(binary), "capabilities"], check=True, capture_output=True)
    profile = d / "profile.txt"
    subprocess.run(["go", "tool", "covdata", "textfmt", "-i", str(d), "-o", str(profile)], check=True,
                   cwd=ADAPTER_DIR, capture_output=True)
    for line in profile.read_text().splitlines()[1:]:
        loc = line.rsplit(" ", 2)[0]
        if loc.startswith(HCL + "/"):
            blocks[loc[len(HCL) + 1:]] = []
    for i, (_o, _m, locs) in enumerate(results):
        for loc in locs:
            blocks[loc].append(i)
    cache.write_text(json.dumps({"fingerprint": fp, "blocks": blocks}))
    return blocks


def block_index(blocks, package):
    """Blocks by file of one package: [((start line, col), (end line, col), tests), ...]."""
    by_file = defaultdict(list)
    for loc, tests in blocks.items():
        f, span = loc.rsplit(":", 1)
        if os.path.dirname(f) != package_dir(package):
            continue
        start, end = span.split(",")
        sl, sc = map(int, start.split("."))
        el, ec = map(int, end.split("."))
        by_file[os.path.basename(f)].append(((sl, sc), (el, ec), tests))
    return by_file


def covering_tests(mutant, by_file):
    """Indexes of the tests that execute a mutant's line, or None if that can't be told."""
    blocks = by_file.get(mutant["cov_file"], [])
    line, col = mutant["line"], mutant["col"]
    if col == 0:  # a //line directive without columns (Ragel); match by line
        hits = [b for b in blocks if b[0][0] <= line <= b[1][0]]
        return sorted(set().union(*(b[2] for b in hits))) if hits else None
    hits = [b for b in blocks if b[0] <= (line, col) <= b[1]]
    if hits:
        # Blocks only overlap around function literals, where the innermost is right.
        return min(hits, key=lambda b: (b[1][0] - b[0][0], b[1][1] - b[0][1]))[2]
    # Some code is in no block, such as the condition of an else if; any test
    # that executes the function may reach it.
    if mutant.get("func_lines"):
        first, last = mutant["func_lines"]
        inside = [b for b in blocks if first <= b[0][0] and b[1][0] <= last]
        if inside:
            return sorted(set().union(*(b[2] for b in inside)))
    return None


# Mutants

def generate(work, env, package):
    binary = work / "mutate"
    go(["build", "-o", str(binary), "."], cwd=ROOT / "tools" / "mutate")
    out = subprocess.run([str(binary), "-dir", str(ADAPTER_DIR), "-modfile", str(env["modfile"]),
                          "-pkg", import_path(package)], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


STATE = {}


def init_worker(state):
    STATE.update(state)


def apply(mutant, wdir):
    """Writes the mutated file and an overlay that puts it in the hcl copy."""
    src_path = STATE["copy"] / package_dir(STATE["package"]) / mutant["file"]
    src = src_path.read_bytes()
    mutated = wdir / mutant["file"]
    mutated.write_bytes(src[:mutant["start"]] + mutant["replacement"].encode() + src[mutant["end"]:])
    overlay = wdir / "overlay.json"
    overlay.write_text(json.dumps({"Replace": {str(src_path): str(mutated)}}))
    return overlay


def error_counts(out):
    return Counter(str(e.get("message", "")) for e in (out or {}).get("errors") or [])


def runner_view(out, op):
    """What the runner compares: validity, the phase of an error, or the normalized body."""
    if not isinstance(out, dict) or not isinstance(out.get("valid"), bool):
        return out
    if not out["valid"]:
        return {"valid": False, "phase": out.get("phase")}
    try:
        return {"valid": True, "body": hcltest.canonical(hcltest.normalize_body(out.get("body"), op))}
    except hcltest.AdapterError as e:
        return {"valid": True, "malformed": str(e)}


def without_errors(out):
    """Output as the runner compares it: errors only count through "valid" and "phase"."""
    return {k: v for k, v in out.items() if k != "errors"} if isinstance(out, dict) else out


def mutant_worker(mutant):
    """Builds and tests one mutant. A survivor's output on every covering test is
    also compared with hashicorp/hcl's, to sort it before triage."""
    t0 = time.time()
    res = {"key": mutant["key"]}
    tests, base = STATE["tests"], STATE["base"]
    idx = covering_tests(mutant, STATE["by_file"])
    if idx is not None and not idx:
        res["status"] = "not_reached"
        return res
    if idx is None:  # code outside any block, such as a package variable; run everything
        idx = list(range(len(tests)))
    wdir = STATE["work"] / "workers" / str(mp.current_process()._identity[0])
    wdir.mkdir(parents=True, exist_ok=True)
    binary = wdir / "adapter"
    result = build(STATE["env"], binary, overlay=apply(mutant, wdir))
    if result.returncode:
        res["status"] = "compile_error"
        res["detail"] = (result.stderr.strip().splitlines() or [""])[-1]
        return res
    tmp = wdir / "ctx"
    tmp.mkdir(exist_ok=True)
    # Tests that execute less code are more focused, so they come first.
    order = sorted(idx, key=lambda i: (STATE["size"][i], i))
    verdict, error_changes, diff_tests = "same_output", set(), []
    for n, i in enumerate(order, 1):
        outcome, message, out = run_one(binary, tests[i], tmp)
        if outcome != "pass":
            res.update(status="killed", by=tests[i].name, how=outcome if outcome == "fail" else message[:200],
                       ran=n, secs=round(time.time() - t0, 2))
            return res
        if out == base[i]:
            continue
        if without_errors(out) != without_errors(base[i]):
            verdict = "hidden_diff"
        elif verdict == "same_output":
            verdict = "errors_only"
        b, m = error_counts(base[i]), error_counts(out)
        if b == m:
            error_changes.add("ranges")
        elif sum(m.values()) < sum(b.values()) and not m - b:
            error_changes.add("fewer")
        elif sum(m.values()) > sum(b.values()) and not b - m:
            error_changes.add("more")
        else:
            error_changes.add("messages")
        if len(diff_tests) < 5:
            diff_tests.append(tests[i].name)
    res.update(status="survived", verdict=verdict, ran=len(order), secs=round(time.time() - t0, 2))
    if diff_tests:
        res.update(error_changes=sorted(error_changes), diff_tests=diff_tests)
    return res


def cmd_run(args):
    work = args.work
    env = setup(work)
    tests = hcltest.discover([hcltest.TESTS_DIR])
    fp = tests_fingerprint()
    blocks = per_test_coverage(work, env, tests, fp, args.workers)
    base = base_outputs(work, env, tests, fp)
    mutants = generate(work, env, args.package)
    d = pkg_dir(work, args.package)
    (d / "mutants.json").write_text(json.dumps(mutants))
    header = {"fingerprint": fp, "mutants": hashlib.sha256(json.dumps(mutants).encode()).hexdigest()[:16],
              "version": env["version"]}
    results_path = d / "results.jsonl"
    done = set()
    if results_path.exists():
        lines = results_path.read_text().splitlines()
        if lines and json.loads(lines[0]) == header:
            done = {json.loads(line)["key"] for line in lines[1:] if line.strip()}
    if not done:
        results_path.write_text(json.dumps(header) + "\n")
    todo = [m for m in mutants if m["key"] not in done]
    size = Counter()
    for test_ids in blocks.values():
        for i in test_ids:
            size[i] += 1
    state = {"work": work, "env": env, "copy": env["copy"], "package": args.package, "tests": tests, "base": base,
             "by_file": block_index(blocks, args.package), "size": size}
    print(f"{len(mutants)} mutants of {args.package}, {len(todo)} to run", flush=True)
    started = time.time()
    with results_path.open("a") as out, mp.Pool(args.workers, initializer=init_worker, initargs=(state,)) as pool:
        for n, res in enumerate(pool.imap_unordered(mutant_worker, todo), 1):
            out.write(json.dumps(res) + "\n")
            out.flush()
            if n % 200 == 0:
                print(f"  {n}/{len(todo)} after {time.time() - started:.0f}s", flush=True)
    return report(args.work, args.package)


# Reporting

def classification_path(package):
    return ROOT / "coverage" / "mutation" / (slug(package) + ".json")


def load_classifications(package):
    path = classification_path(package)
    if not path.exists():
        return {"package": import_path(package), "version": None, "survivors": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_run(work, package):
    d = pkg_dir(work, package)
    if not (d / "results.jsonl").exists():
        sys.exit(f"no results for {package}: run python3 tools/mutation.py run {package}")
    mutants = {m["key"]: m for m in json.loads((d / "mutants.json").read_text())}
    lines = (d / "results.jsonl").read_text().splitlines()
    header = json.loads(lines[0])
    results = {r["key"]: r for r in map(json.loads, filter(str.strip, lines[1:]))}
    return header, mutants, results


def report(work, package, files=False):
    header, mutants, results = load_run(work, package)
    classes = load_classifications(package)
    status = Counter(r["status"] for r in results.values())
    survivors = {k for k, r in results.items() if r["status"] == "survived"}
    by_key = {c["mutant"]: c for c in classes["survivors"]}
    stale = [k for k, c in by_key.items() if k not in survivors or c["source"] != mutants[k]["source"]]
    classified = Counter(by_key[k]["class"] for k in survivors if k in by_key and k not in stale)
    unclassified = [k for k in survivors if k not in by_key or k in stale]
    killed = status["killed"]
    visible = killed + classified["gap"] + len(unclassified)
    print(f"{HCL if package == '.' else package} at {header['version']}: {len(results)} of {len(mutants)} mutants run")
    print(f"  killed          {killed:>5}  ({sum(1 for r in results.values() if 'timed out' in r.get('how', ''))} "
          "by timing out)")
    print(f"  survived        {status['survived']:>5}")
    for cls in CLASSES:
        if classified[cls]:
            print(f"    {cls:<14}{classified[cls]:>5}")
    print(f"    unclassified  {len(unclassified):>5}")
    print(f"  not reached     {status['not_reached']:>5}  (no test executes the line)")
    print(f"  compile errors  {status['compile_error']:>5}")
    if killed + status["survived"]:
        print(f"score: {killed / (killed + status['survived']):.1%} of mutants that tests reach are killed")
    if visible:
        print(f"       {killed / visible:.1%} of the ones the protocol can see "
              f"({killed} of {visible}, counting unclassified survivors as visible)")
    if stale:
        print(f"{len(stale)} classifications in {classification_path(package).relative_to(ROOT)} no longer match a "
              "survivor (killed now, or the code changed); remove them")
    if files:
        rows = defaultdict(Counter)
        for k, r in results.items():
            m = mutants[k]
            cls = r["status"] if r["status"] != "survived" else ("unclassified" if k in unclassified else
                                                                  by_key[k]["class"])
            rows[m["cov_file"]][cls] += 1
        cols = ["killed", "gap", "unclassified"] + sorted(INVISIBLE) + ["not_reached", "compile_error"]
        print("\n" + f"{'file':<24}" + "".join(f"{c[:8]:>9}" for c in cols))
        for f in sorted(rows, key=lambda f: -sum(rows[f].values())):
            print(f"{f:<24}" + "".join(f"{rows[f][c]:>9}" for c in cols))
    return 0


def cmd_report(args):
    return report(args.work, args.package, args.files)


def cmd_survivors(args):
    _header, mutants, results = load_run(args.work, args.package)
    by_key = {c["mutant"]: c for c in load_classifications(args.package)["survivors"]}
    rows = []
    for k, r in sorted(results.items(), key=lambda kv: mutants[kv[0]]["id"]):
        if r["status"] != "survived":
            continue
        c = by_key.get(k)
        if c and c["source"] == mutants[k]["source"] and not args.all:
            continue
        m = mutants[k]
        row = {f: m[f] for f in ("id", "key", "op", "file", "cov_file", "line", "func", "orig", "replacement",
                                 "source")}
        row.update({f: r[f] for f in ("verdict", "error_changes", "diff_tests") if f in r})
        if c:
            row.update(**{"class": c["class"], "reason": c["reason"]})
        rows.append(row)
    if args.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    for row in rows:
        cls = f" [{row['class']}]" if "class" in row else ""
        print(f"#{row['id']} {row['cov_file']}:{row['line']} {row['func']} {row['op']}: {row['orig']!r} -> "
              f"{row['replacement']!r} ({row['verdict']}){cls}")
    print(f"{len(rows)} survivors")
    return 0


def find_mutant(mutants, ref):
    if ref.isdigit():
        found = [m for m in mutants.values() if m["id"] == int(ref)]
    else:
        found = [mutants[ref]] if ref in mutants else []
    if not found:
        sys.exit(f"no mutant {ref}")
    return found[0]


def cmd_try(args):
    env = setup(args.work)
    _header, mutants, _results = load_run(args.work, args.package)
    m = find_mutant(mutants, args.mutant)
    base_bin = args.work / "base-adapter"
    if not base_bin.exists() and build(env, base_bin).returncode:
        sys.exit("building the adapter failed")
    wdir = pkg_dir(args.work, args.package) / "try" / str(m["id"])
    wdir.mkdir(parents=True, exist_ok=True)
    STATE.update(copy=env["copy"], package=args.package)
    binary = wdir / "adapter"
    if not binary.exists():
        result = build(env, binary, overlay=apply(m, wdir))
        if result.returncode:
            sys.exit("the mutant doesn't compile:\n" + result.stderr)
    print(f"mutant #{m['id']} {m['key']} ({m['func']}): {m['orig']!r} -> {m['replacement']!r}")
    outs = []
    for label, b in (("hashicorp/hcl", base_bin), ("mutant", binary)):
        cmd = [str(b), args.op, str(args.input)] + ([str(args.context)] if args.context else [])
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT * 2)
        try:
            out = json.loads(p.stdout)
        except ValueError:
            out = {"crash": p.returncode, "stderr": p.stderr[-500:]}
        outs.append(out)
        print(f"--- {label}\n{json.dumps(out, ensure_ascii=False, sort_keys=True)[:4000]}")
    visible = [runner_view(o, args.op) for o in outs]
    print("DIFFERENT" if visible[0] != visible[1] else "SAME for the runner (the raw outputs differ)"
          if outs[0] != outs[1] else "SAME")
    return 0


def cmd_classify(args):
    env = setup(args.work)
    _header, mutants, results = load_run(args.work, args.package)
    data = load_classifications(args.package)
    data["package"], data["version"] = import_path(args.package), env["version"]
    by_key = {c["mutant"]: c for c in data["survivors"]}
    added = 0
    for line in args.records.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        m = mutants[rec["key"]] if "key" in rec else find_mutant(mutants, str(rec["id"]))
        if results.get(m["key"], {}).get("status") != "survived":
            sys.exit(f"{m['key']} didn't survive the last run")
        if rec["class"] not in CLASSES:
            sys.exit(f"{m['key']}: unknown class {rec['class']!r}")
        if not str(rec.get("reason", "")).strip():
            sys.exit(f"{m['key']}: needs a reason")
        by_key[m["key"]] = {"mutant": m["key"], "source": m["source"], "class": rec["class"],
                            "reason": rec["reason"].strip()}
        added += 1
    order = {k: m["id"] for k, m in mutants.items()}
    data["survivors"] = sorted(by_key.values(), key=lambda c: (order.get(c["mutant"], len(order)), c["mutant"]))
    path = classification_path(args.package)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"classified {added} survivors; {path.relative_to(ROOT)} has {len(data['survivors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
