"""Install compatible PyPI wheels using Windows curl TLS, validating PyPI SHA-256.

Useful with the project's old Python/OpenSSL build. Never disables TLS verification.
Usage: .venv-rfq-data/Scripts/python scripts/install_local_dependencies.py streamlit
"""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.tags import sys_tags
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
from pip._vendor.packaging.version import InvalidVersion, Version


def main():
    targets = sys.argv[1:] or ["streamlit"]
    root = Path(tempfile.mkdtemp(prefix="rfq-dependency-wheels-"))
    tags, env = set(sys_tags()), default_environment()
    cache, selected, constraints, extras = {}, {}, {}, {}
    def fetch(url, target):
        subprocess.run(["curl.exe", "--fail", "--location", "--silent", "--show-error",
                        "--connect-timeout", "15", "--max-time", "180", "--retry", "2",
                        "--output", str(target), url], check=True)
    def metadata(name, version=None):
        key = (name, version)
        if key not in cache:
            target = root / (name + ("-" + version if version else "") + ".json")
            fetch("https://pypi.org/pypi/" + name + ("/" + version if version else "") + "/json", target)
            cache[key] = json.loads(target.read_text(encoding="utf-8"))
        return cache[key]
    queue = [Requirement(t) for t in targets]
    while queue:
        req = queue.pop(0)
        name = canonicalize_name(req.name)
        constraints.setdefault(name, set()).add(str(req.specifier))
        extras.setdefault(name, set()).update(req.extras)
        spec = SpecifierSet(",".join(s for s in constraints[name] if s))
        if name in selected and Version(selected[name]) in spec:
            continue
        index, chosen, versions = metadata(name), None, []
        for text in index["releases"]:
            try:
                v = Version(text)
                if not v.is_prerelease and v in spec:
                    versions.append(v)
            except InvalidVersion:
                pass
        for v in sorted(versions, reverse=True):
            for f in sorted(index["releases"][str(v)], key=lambda f: f["filename"]):
                if f.get("yanked") or f.get("packagetype") != "bdist_wheel":
                    continue
                if f.get("requires_python") and not SpecifierSet(f["requires_python"]).contains(env["python_full_version"]):
                    continue
                if not (parse_wheel_filename(f["filename"])[3] & tags):
                    continue
                chosen = (str(v), f)
                break
            if chosen:
                break
        if not chosen:
            raise RuntimeError("No compatible wheel: " + str(req))
        version, file = chosen
        target = root / file["filename"]
        fetch(file["url"], target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != file["digests"]["sha256"]:
            raise RuntimeError("Wheel SHA-256 mismatch: " + name)
        selected[name] = version
        print(name + "==" + version, flush=True)
        for text in metadata(name, version)["info"].get("requires_dist") or []:
            dep = Requirement(text)
            if dep.marker is None or any(dep.marker.evaluate(dict(env, extra=e)) for e in (extras[name] or {""})):
                queue.append(dep)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(root)] + targets, check=True)


if __name__ == "__main__":
    main()
