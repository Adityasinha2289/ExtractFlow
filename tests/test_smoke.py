import extractflow
from extractflow.cli.main import cli
from extractflow.exporters.json_exporter import export_json
from extractflow.extractors.html import HTMLExtractor
from extractflow.validators.schema_validator import validate


def test_package_imports():
    assert extractflow is not None


def test_cli(capsys):
    cli()
    captured = capsys.readouterr()
    assert "ExtractFlow CLI" in captured.out


def test_extractor_creation():
    extractor = HTMLExtractor({"key": "value"})
    assert extractor.mapping == {"key": "value"}


def test_validator():
    assert callable(validate)
    validate({}, {})


def test_exporter(tmp_path):
    assert callable(export_json)
    # export_json now really writes, so give it a temp path rather than
    # dropping a dummy.json into the repo root on every test run.
    export_json({}, str(tmp_path / "dummy.json"))
