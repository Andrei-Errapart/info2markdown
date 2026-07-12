import pytest
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption(
        "--keep-artifacts",
        metavar="DIR",
        default=None,
        help="Write test outputs to DIR/<test-id>/ instead of discarding.",
    )
    parser.addoption(
        "--known-defects",
        action="store_true",
        default=False,
        help="Run tests marked known_defect (they assert correct behavior and fail until the converter is fixed).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--known-defects"):
        return
    skip = pytest.mark.skip(reason="known defect (red by design); run with --known-defects")
    for item in items:
        if "known_defect" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def artifact_dir(request, tmp_path: Path) -> Path:
    keep = request.config.getoption("--keep-artifacts")
    if keep is None:
        return tmp_path
    safe = request.node.nodeid.replace("/", "__").replace("::", "__").replace(" ", "_")
    out = Path(keep) / safe
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture(scope="module")
def module_artifact_dir(request, tmp_path_factory) -> Path:
    """Per-module artifact dir for tests sharing one expensive conversion."""
    keep = request.config.getoption("--keep-artifacts")
    safe = request.module.__name__.replace(".", "__")
    if keep is None:
        return tmp_path_factory.mktemp(safe)
    out = Path(keep) / safe
    out.mkdir(parents=True, exist_ok=True)
    return out
