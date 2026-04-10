import re
import sys
from pathlib import Path

VERSION_RE: re.Pattern[str] = re.compile(
    pattern=r'^version\s*=\s*"(\d+\.\d+\.\d+)"$', flags=re.MULTILINE
)


def read_version(path: str) -> str:
    text: str = Path(path).read_text()
    match: re.Match[str] | None = VERSION_RE.search(string=text)
    if match is None:
        raise SystemExit(f"Could not find project version in {path}")
    return match.group(1)


def parse_version(value: str) -> tuple[int, int, int]:
    parts: list[str] = value.split(sep=".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(f"Unsupported version format: {value}")
    major, minor, patch = parts
    return int(major), int(minor), int(patch)


if __name__ == "__main__":
    base_path, head_path = sys.argv[1], sys.argv[2]

    base: str = read_version(path=base_path)
    head: str = read_version(path=head_path)

    if parse_version(value=head) <= parse_version(value=base):
        raise SystemExit(
            f"Version was not bumped. Base version is {base}, PR version is {head}. "
            "You need to increase the version number in your branch."
        )

    print(f"Version bump detected: FROM:{base} TO:{head}")
