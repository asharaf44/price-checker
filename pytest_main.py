"""Entry point so a Bazel py_test runs pytest. Test files are passed as args."""

import sys

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:]))
