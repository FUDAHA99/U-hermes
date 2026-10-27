"""Hard resource limits for the short-lived document worker, before decoding.

Windows uses a Job Object; Unix uses rlimits. This is resource containment, not
an OS security sandbox. No untrusted document may choose limits or model paths.
"""
import os
import sys

MEMORY_BYTES = 1536 * 1024 * 1024
EXTRACT_SECONDS = 180
_job_handle = None


def _windows_limits():
    import ctypes
    from ctypes import wintypes as w

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
                    ("SchedulingClass", w.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
    limits = Extended()
    # PROCESS_TIME | ACTIVE_PROCESS | PROCESS_MEMORY. Disallow parser children.
    limits.BasicLimitInformation.LimitFlags = 0x2 | 0x8 | 0x100
    limits.BasicLimitInformation.PerProcessUserTimeLimit = EXTRACT_SECONDS * 10_000_000
    limits.BasicLimitInformation.ActiveProcessLimit = 1
    limits.ProcessMemoryLimit = MEMORY_BYTES
    if (not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise OSError(error, "document resource limits")
    # Kept alive until process exit. No external executable or system install.
    global _job_handle
    _job_handle = handle


def restrict_documents():
    if os.name == "nt":
        _windows_limits()
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (EXTRACT_SECONDS, EXTRACT_SECONDS))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def offline(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen",
                     "os.system", "os.posix_spawn", "os.exec", "os.spawn"}:
            raise PermissionError("document parsing is offline and cannot launch programs")

    sys.addaudithook(offline)
