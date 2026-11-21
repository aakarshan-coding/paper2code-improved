"""Download the git-lfs files of one PaperBench paper directly from GitHub, checking each against its LFS hash.

`git lfs fetch` on a partial clone fetches tree blobs one at a time and is very slow;
this fetches only the files of the paper you name, at the commit the clone is on.
"""
import argparse
import hashlib
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "benchmarks" / "frontier-evals"
PAPERS_REL = "project/paperbench/data/papers"
POINTER_HEADER = b"version https://git-lfs.github.com/spec/v1"


def parse_pointer(data: bytes) -> tuple:
    fields = dict(line.split(" ", 1) for line in data.decode().splitlines() if " " in line)
    return fields["oid"].removeprefix("sha256:"), int(fields["size"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paper")
    paper = parser.parse_args().paper

    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    paper_dir = REPO / PAPERS_REL / paper
    pointers = [p for p in sorted(paper_dir.rglob("*")) if p.is_file() and p.read_bytes()[:len(POINTER_HEADER)] == POINTER_HEADER]
    for path in pointers:
        oid, size = parse_pointer(path.read_bytes())
        rel = path.relative_to(REPO).as_posix()
        url = f"https://media.githubusercontent.com/media/openai/frontier-evals/{commit}/{rel}"
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        if len(data) != size or hashlib.sha256(data).hexdigest() != oid:
            raise SystemExit(f"hash/size mismatch for {rel}")
        path.write_bytes(data)
        print(f"ok {rel} ({size} bytes)")
    print(f"hydrated {len(pointers)} files for {paper} at {commit}")


if __name__ == "__main__":
    main()
