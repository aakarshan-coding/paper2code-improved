"""Copy the agent-visible files for one PaperBench paper into tasks/<paper>/paper/.

Only the files PaperBench gives its own agents are copied. Rubrics and judge
addenda stay inside benchmarks/, which is never mounted into an agent container.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "benchmarks" / "frontier-evals" / "project" / "paperbench" / "data" / "papers"
AGENT_VISIBLE = ["paper.pdf", "paper.md", "addendum.md", "blacklist.txt", "assets"]
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paper")
    paper = parser.parse_args().paper

    src = PAPERS / paper
    if not src.is_dir():
        raise SystemExit(f"{paper} is not in PaperBench: {src} does not exist")

    dst = ROOT / "tasks" / paper / "paper"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    for name in AGENT_VISIBLE:
        if (src / name).is_dir():
            shutil.copytree(src / name, dst / name)
        elif (src / name).exists():
            shutil.copy2(src / name, dst / name)

    files = sorted(p for p in dst.rglob("*") if p.is_file())
    pointers = [str(p.relative_to(dst)) for p in files if p.read_bytes().startswith(LFS_POINTER)]
    if pointers:
        raise SystemExit(f"git-lfs files not hydrated (run git lfs fetch/checkout): {pointers}")
    forbidden = [p.name for p in files if p.name.startswith(("rubric", "judge"))]
    if forbidden:
        raise SystemExit(f"grading files leaked into the task folder: {forbidden}")

    manifest = {
        "paper": paper,
        "source": str(src.relative_to(ROOT)),
        "files": {str(p.relative_to(dst)).replace("\\", "/"): sha256(p) for p in files},
        "blacklist": [line.strip() for line in (dst / "blacklist.txt").read_text().splitlines() if line.strip()],
    }
    (dst.parent / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"prepared {dst} ({len(files)} files); blacklist={manifest['blacklist']}")


if __name__ == "__main__":
    main()
