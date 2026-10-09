"""Run with the intended interpreter before pip, including stock macOS Python 3.9."""

import sys


def check_version(version):
    if tuple(version[:2]) < (3, 11):
        actual = ".".join(str(part) for part in version[:3])
        raise SystemExit("search-first needs Python 3.11 or newer; you have " + actual)


if __name__ == "__main__":
    check_version(sys.version_info)
    print("Python version OK for search-first")
