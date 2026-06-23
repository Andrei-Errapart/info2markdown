import pytest
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption(
        "--keep-artifacts",
        metavar="DIR",
        default=None,
        help="Write test outputs to DIR/<test-id>/ instead of discarding.",
    )


@pytest.fixture()
def artifact_dir(request, tmp_path: Path) -> Path:
    keep = request.config.getoption("--keep-artifacts")
    if keep is None:
        return tmp_path
    safe = request.node.nodeid.replace("/", "__").replace("::", "__").replace(" ", "_")
    out = Path(keep) / safe
    out.mkdir(parents=True, exist_ok=True)
    return out
