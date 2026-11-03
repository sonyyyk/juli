from types import SimpleNamespace
from unittest.mock import MagicMock

import psutil
import pytest

from app.tools.processes import get_process_info as process_info_module
from app.tools.processes import list_processes as list_processes_module
from app.tools.result import make_tool_result
from app.tools.system import cpu, disk, memory
from app.tools.system import state as system_state


def ok(tool, value=10.0):
    return make_tool_result(tool, "success", data={"usage_percent": value})


def failed(tool, code):
    return make_tool_result(tool, "error", error={"code": code, "message": f"{code} msg"})


def patch_tools(monkeypatch, cpu_result, memory_result, disk_result):
    monkeypatch.setattr(system_state, "get_cpu_usage", lambda: cpu_result)
    monkeypatch.setattr(system_state, "get_memory_usage", lambda: memory_result)
    monkeypatch.setattr(system_state, "get_disk_usage", lambda: disk_result)


# --- make_tool_result -------------------------------------------------------


def test_make_tool_result_normalizes_error_data_to_none():
    result = make_tool_result(
        "t", "error", data={}, error={"code": "E", "message": "m"}
    )
    assert result["data"] is None
    assert result["error"]["code"] == "E"


def test_make_tool_result_keeps_data_for_success_and_partial():
    assert make_tool_result("t", "success", data={"a": 1})["data"] == {"a": 1}
    partial = make_tool_result(
        "t", "partial", data={"a": 1}, error={"code": "P", "message": "m"}
    )
    assert partial["data"] == {"a": 1}


@pytest.mark.parametrize("status", ["ok", "", None, "SUCCESS"])
def test_make_tool_result_rejects_unknown_status(status):
    with pytest.raises(ValueError, match="Unknown tool status"):
        make_tool_result("t", status)


def test_make_tool_result_rejects_success_with_error():
    with pytest.raises(ValueError, match="must not carry an error"):
        make_tool_result("t", "success", data={}, error={"code": "E", "message": "m"})


# --- get_system_state -------------------------------------------------------


def test_system_state_success(monkeypatch):
    patch_tools(
        monkeypatch,
        ok("get_cpu_usage", 1.0),
        ok("get_memory_usage", 2.0),
        ok("get_disk_usage", 3.0),
    )

    result = system_state.get_system_state()

    assert result["tool"] == "get_system_state"
    assert result["status"] == "success"
    assert result["error"] is None
    assert result["data"] == {
        "cpu": {"usage_percent": 1.0},
        "memory": {"usage_percent": 2.0},
        "disk": {"usage_percent": 3.0},
    }


def test_system_state_partial_reports_failed_resources(monkeypatch):
    patch_tools(
        monkeypatch,
        failed("get_cpu_usage", "CPU_READ_ERROR"),
        ok("get_memory_usage", 2.0),
        failed("get_disk_usage", "DISK_READ_ERROR"),
    )

    result = system_state.get_system_state()

    assert result["status"] == "partial"
    assert result["data"] == {
        "cpu": None,
        "memory": {"usage_percent": 2.0},
        "disk": None,
    }
    error = result["error"]
    assert error["code"] == "SYSTEM_STATE_PARTIAL"
    assert "cpu: CPU_READ_ERROR" in error["message"]
    assert "disk: DISK_READ_ERROR" in error["message"]
    assert "memory" not in error["details"]
    assert error["details"]["cpu"] == {
        "code": "CPU_READ_ERROR",
        "message": "CPU_READ_ERROR msg",
    }
    assert error["details"]["disk"]["code"] == "DISK_READ_ERROR"


def test_system_state_error_when_all_resources_fail(monkeypatch):
    patch_tools(
        monkeypatch,
        failed("get_cpu_usage", "CPU_READ_ERROR"),
        failed("get_memory_usage", "MEMORY_READ_ERROR"),
        failed("get_disk_usage", "DISK_READ_ERROR"),
    )

    result = system_state.get_system_state()

    assert result["status"] == "error"
    assert result["data"] is None
    error = result["error"]
    assert error["code"] == "SYSTEM_STATE_UNAVAILABLE"
    assert set(error["details"]) == {"cpu", "memory", "disk"}
    assert error["details"]["memory"]["code"] == "MEMORY_READ_ERROR"


def test_system_state_handles_invalid_child_results(monkeypatch):
    def raising():
        raise OSError("boom")

    patch_tools(monkeypatch, "not a mapping", ok("get_memory_usage"), ok("get_disk_usage"))
    monkeypatch.setattr(system_state, "get_disk_usage", raising)

    result = system_state.get_system_state()

    assert result["status"] == "partial"
    assert result["data"]["cpu"] is None
    assert result["data"]["disk"] is None
    assert result["error"]["details"]["cpu"]["code"] == "INVALID_TOOL_RESULT"
    assert result["error"]["details"]["disk"] == {
        "code": "TOOL_EXCEPTION",
        "message": "boom",
    }


# --- data is None on error for every tool -----------------------------------


@pytest.mark.parametrize(
    ("module", "function", "psutil_name", "code"),
    [
        (cpu, "get_cpu_usage", "cpu_percent", "CPU_READ_ERROR"),
        (memory, "get_memory_usage", "virtual_memory", "MEMORY_READ_ERROR"),
        (disk, "get_disk_usage", "disk_usage", "DISK_READ_ERROR"),
    ],
)
def test_resource_tool_error_has_no_data(
    monkeypatch, module, function, psutil_name, code
):
    def raising(*args, **kwargs):
        raise OSError("read failed")

    monkeypatch.setattr(module.psutil, psutil_name, raising)

    result = getattr(module, function)()

    assert result["status"] == "error"
    assert result["data"] is None
    assert result["error"]["code"] == code
    assert result["error"]["message"] == "read failed"


@pytest.mark.parametrize("pid", [0, -5, True, "1", 1.5, None])
def test_get_process_info_invalid_pid_has_no_data(pid):
    result = process_info_module.get_process_info(pid)

    assert result["tool"] == "get_process_info"
    assert result["status"] == "error"
    assert result["data"] is None
    assert result["error"]["code"] == "INVALID_PID"


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (psutil.NoSuchProcess(123), "PROCESS_NOT_FOUND"),
        (psutil.AccessDenied(123), "ACCESS_DENIED"),
    ],
)
def test_get_process_info_errors_have_no_data(monkeypatch, exception, code):
    def raising(pid):
        raise exception

    monkeypatch.setattr(process_info_module.psutil, "Process", raising)

    result = process_info_module.get_process_info(123)

    assert result["status"] == "error"
    assert result["data"] is None
    assert result["error"]["code"] == code


def test_get_process_info_success_shape(monkeypatch):
    process = MagicMock()
    process.pid = 42
    process.name.return_value = "demo"
    process.status.return_value = "running"
    process.cpu_percent.return_value = 1.5
    process.memory_info.return_value = SimpleNamespace(rss=100, vms=200)
    process.create_time.return_value = 1700000000.0
    monkeypatch.setattr(process_info_module.psutil, "Process", lambda pid: process)

    result = process_info_module.get_process_info(42)

    assert result["status"] == "success"
    assert result["error"] is None
    assert result["data"]["pid"] == 42
    assert result["data"]["memory_rss_bytes"] == 100


def fake_process(pid, memory_info="skip", raises=None):
    info = {"pid": pid, "name": f"p{pid}", "status": "running"}
    if memory_info != "skip":
        info["memory_info"] = memory_info
    process = MagicMock()
    if raises is None:
        process.info = info
    else:
        type(process).info = property(lambda self: (_ for _ in ()).throw(raises))
    return process


def test_list_processes_success_and_partial(monkeypatch):
    ok_process = fake_process(1, SimpleNamespace(rss=10))
    ok_process.info["memory_info"] = SimpleNamespace(rss=10)
    monkeypatch.setattr(
        list_processes_module.psutil,
        "process_iter",
        lambda **kwargs: iter([ok_process]),
    )

    result = list_processes_module.list_processes()
    assert result["status"] == "success"
    assert result["error"] is None
    assert result["data"]["process_count"] == 1
    assert result["data"]["inaccessible_count"] == 0

    denied = fake_process(2, raises=psutil.AccessDenied(2))
    gone = fake_process(3, raises=psutil.NoSuchProcess(3))
    monkeypatch.setattr(
        list_processes_module.psutil,
        "process_iter",
        lambda **kwargs: iter([ok_process, denied, gone]),
    )

    result = list_processes_module.list_processes()
    assert result["status"] == "partial"
    assert result["error"]["code"] == "ACCESS_DENIED"
    assert result["data"]["process_count"] == 1
    assert result["data"]["inaccessible_count"] == 1
