"""The project's own files."""
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_two_lists_of_dependencies_agree():
    """uv installs what pyproject.toml lists, and Render what requirements.txt lists."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    requirements = [line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()]
    assert sorted(project["dependencies"]) == sorted(requirements)
