from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

spec = spec_from_file_location(
    "check_python", Path(__file__).parents[1] / "scripts" / "check_python.py"
)
check_python = module_from_spec(spec)
spec.loader.exec_module(check_python)


def test_stock_macos_python_gets_actionable_message():
    with pytest.raises(SystemExit, match="search-first needs Python 3.11 or newer; you have 3.9.6"):
        check_python.check_version((3, 9, 6))


@pytest.mark.parametrize("version", [(3, 11, 0), (3, 12, 10), (3, 14, 4)])
def test_supported_python(version):
    check_python.check_version(version)
