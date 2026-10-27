"""Reproducible local-only OCR benchmark using shipped fictional resumes.

Run with the packaged Python: python tools/benchmark_recruit_ocr.py --repeats 3
Each sample starts a fresh resource-limited process, including imports, model
hash verification, rendering and OCR. Peak RSS is the whole child's high-water
mark (Windows: PeakWorkingSetSize), not a Python-only allocation estimate.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parents[1] / "portable" / "scripts"


def peak_mib():
    if os.name != "nt":
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024 * 1024 if sys.platform == "darwin" else 1024)
    import ctypes
    from ctypes import wintypes as w

    class Counters(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Counters), w.DWORD]
    psapi.GetProcessMemoryInfo.restype = w.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize / 1024 / 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--sample", help=argparse.SUPPRESS)
    args = parser.parse_args()
    sys.path.insert(0, str(SCRIPTS))
    if args.sample:
        started = time.perf_counter()
        from recruit_limits import restrict_documents
        restrict_documents()
        from recruit_core import extract_upload
        path = Path(args.sample)
        result = extract_upload({"name": path.name, "content": base64.b64encode(path.read_bytes()).decode("ascii")})
        print(json.dumps({"seconds": round(time.perf_counter() - started, 3),
                          "peak_mib": round(peak_mib(), 1), "characters": len(result["text"]),
                          **result["extraction"]}))
        return
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               NUMEXPR_NUM_THREADS="1", MALLOC_ARENA_MAX="2")
    report = {"python": sys.version.split()[0], "platform": sys.platform,
              "repeats": args.repeats, "results": []}
    for prefix in ("01", "06", "07", "08"):
        path, = (SCRIPTS.parent / "recruit/demo-resumes").glob(prefix + "_*.*")
        samples = []
        for _ in range(args.repeats):
            started = time.perf_counter()
            run = subprocess.run([sys.executable, "-I", __file__, "--sample", str(path)],
                                 env=env, capture_output=True, text=True, encoding="utf-8", timeout=190, check=True)
            value = json.loads(run.stdout.strip().splitlines()[-1])
            value["wall_seconds"] = round(time.perf_counter() - started, 3)
            samples.append(value)
        report["results"].append({"file": path.name, "samples": samples,
                                  "median_wall_seconds": round(statistics.median(s["wall_seconds"] for s in samples), 3),
                                  "maximum_peak_mib": max(s["peak_mib"] for s in samples)})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
