
from pathlib import Path

from app.tools.system.pressure import get_system_pressure


def test_pressure_reads_all_resources(monkeypatch):
    sample_data = {
        "/proc/pressure/cpu": (
            "some avg10=1.25 avg60=0.50 avg300=0.10 total=100\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        ),
        "/proc/pressure/memory": (
            "some avg10=2.00 avg60=1.00 avg300=0.50 total=200\n"
            "full avg10=0.50 avg60=0.25 avg300=0.10 total=50\n"
        ),
        "/proc/pressure/io": (
            "some avg10=3.00 avg60=2.00 avg300=1.00 total=300\n"
            "full avg10=1.00 avg60=0.50 avg300=0.20 total=100\n"
        ),
    }

    def fake_read_text(path, encoding="utf-8"):
        return sample_data[str(path)]

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    result = get_system_pressure()

    assert result["status"] == "success"
    assert result["data"]["unavailable"] == {}
    assert result["data"]["resources"]["cpu"]["some"]["avg10"] == 1.25
    assert result["data"]["resources"]["memory"]["full"]["total"] == 50
    assert result["data"]["resources"]["io"]["some"]["total"] == 300


def test_pressure_reports_unavailable_resource(monkeypatch):
    def fake_read_text(path, encoding="utf-8"):
        if str(path) == "/proc/pressure/memory":
            raise OSError("Permission denied")
        return "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    result = get_system_pressure()

    assert result["status"] == "partial"
    assert "memory" in result["data"]["unavailable"]
    assert result["data"]["resources"]["cpu"]["some"]["total"] == 0


def test_pressure_reports_error_when_all_resources_unavailable(monkeypatch):
    def fake_read_text(path, encoding="utf-8"):
        raise OSError("File unavailable")

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    result = get_system_pressure()

    assert result["status"] == "error"
    assert result["data"] is None
    assert result["error"]["code"] == "PSI_UNAVAILABLE"
