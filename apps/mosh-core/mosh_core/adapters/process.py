from __future__ import annotations

import os
import signal
import subprocess
from typing import Mapping


def _create_windows_job() -> int | None:
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel32.CloseHandle(job)
        return None
    return int(job)


def _assign_windows_job(job: int | None, process: subprocess.Popen[str]) -> int | None:
    if job is None:
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    if kernel32.AssignProcessToJobObject(job, process._handle):
        return job
    return None


def _resume_windows_process(process: subprocess.Popen[str]) -> bool:
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    return ntdll.NtResumeProcess(process._handle) == 0


def _close_windows_job(job: int | None) -> None:
    if job is not None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(job)


def _terminate_process_tree(process: subprocess.Popen[str], windows_job: int | None) -> None:
    if os.name == "nt":
        if windows_job is not None:
            _close_windows_job(windows_job)
        elif process.poll() is None:
            process.kill()
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        process.kill()


def _close_capture_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _discard_unstarted_process(process: subprocess.Popen[str], windows_job: int | None) -> dict:
    _close_windows_job(windows_job)
    if process.poll() is None:
        process.kill()
    try:
        process.communicate(timeout=0.5)
    except subprocess.TimeoutExpired:
        _close_capture_pipes(process)
    return {
        "available": True,
        "exit_code": None,
        "stdout": "",
        "stderr": "process isolation unavailable",
    }


def run(command: list[str], *, timeout: float = 10, env: Mapping[str, str] | None = None) -> dict:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    windows_job = _create_windows_job()
    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
            )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged,
            shell=False,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        if os.name == "nt":
            created_job = windows_job
            windows_job = _assign_windows_job(created_job, process)
            if windows_job is None:
                return _discard_unstarted_process(process, created_job)
            if not _resume_windows_process(process):
                result = _discard_unstarted_process(process, windows_job)
                windows_job = None
                return result
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process, windows_job)
            windows_job = None
            try:
                process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                _close_capture_pipes(process)
                if process.poll() is None:
                    process.kill()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
            return {"available": True, "exit_code": None, "stdout": "", "stderr": "timeout"}
        _close_windows_job(windows_job)
        windows_job = None
        return {
            "available": True,
            "exit_code": process.returncode,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }
    except FileNotFoundError:
        _close_windows_job(windows_job)
        return {"available": False, "exit_code": None, "stdout": "", "stderr": "command not found"}
    except PermissionError:
        _close_windows_job(windows_job)
        return {"available": False, "exit_code": None, "stdout": "", "stderr": "permission denied"}
