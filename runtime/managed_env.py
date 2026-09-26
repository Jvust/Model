"""Private, reusable inference environments; never pip-install into the user's Python."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

LOCK = threading.Lock()
PACKAGES = {
    "got_ocr2": ["transformers==4.57.6", "accelerate==1.10.1", "Pillow==11.3.0", "numpy==1.26.4"],
    "chronos_2": ["chronos-forecasting==2.2.1", "transformers==4.57.6", "accelerate==1.10.1", "pandas==2.2.3", "numpy==1.26.4"],
    "timesfm_2_0_500m": ["timesfm[torch]==1.3.0", "numpy==1.26.4", "pandas==2.2.3"],
    "wan22_t2v_a14b": ["diffusers==0.35.1", "transformers==4.57.6", "accelerate==1.10.1", "sentencepiece==0.2.1", "imageio-ffmpeg==0.6.0", "Pillow==11.3.0", "numpy==1.26.4"],
}
PACKAGES["wan22_i2v_a14b"] = PACKAGES["wan22_t2v_a14b"]


def check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError("Task cancelled")


def extract_safe(archive, destination):
    root = Path(destination).resolve()
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            target = (root / entry.filename).resolve()
            if not target.is_relative_to(root) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe archive entry")
        zipped.extractall(root)


def download(url, path, cancel, sha256=None, limit=64 * 1024**2):
    """Small dependency artifacts only. Model weights never use this function."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".part")
    digest = hashlib.sha256()
    check_cancel(cancel)
    request = Request(url, headers={"User-Agent": "JvustModel/0.17"})
    with urlopen(request, timeout=30) as response, temporary.open("wb") as handle:
        total = 0
        while chunk := response.read(1024 * 1024):
            check_cancel(cancel)
            total += len(chunk)
            if total > limit:
                raise ValueError("Dependency artifact exceeds its size limit")
            handle.write(chunk)
            digest.update(chunk)
    if sha256 and digest.hexdigest() != sha256:
        raise ValueError("Dependency SHA256 mismatch")
    temporary.replace(path)


def run_checked(command, cancel, log_path, timeout=1800, env=None):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with Path(log_path).open("ab") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, env=env, creationflags=flags)
        started = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Dependency setup timed out; see the local setup log")
                time.sleep(0.2)
            if process.returncode:
                raise RuntimeError(f"Dependency setup failed ({process.returncode}); see {Path(log_path).name}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)


def ensure_environment(root, model_id, cancel, progress=lambda text: None):
    packages = PACKAGES[model_id]
    gpu = model_id.startswith("wan22_")
    spec = {"packages": packages, "torch": "2.7.1", "torchvision": "0.22.1", "gpu": gpu, "python": "3.11.9"}
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]
    destination = Path(root) / "environments" / key
    destination.mkdir(parents=True, exist_ok=True)
    interpreter = destination / ("python.exe" if os.name == "nt" else "bin/python")
    marker = destination / "READY.json"
    log = destination / "setup.log"
    while not LOCK.acquire(timeout=0.2):
        check_cancel(cancel)
    try:
        check_cancel(cancel)
        if marker.is_file() and interpreter.is_file():
            return interpreter
        progress("Preparing a private Python environment")
        if not interpreter.exists():
            if os.name == "nt":
                archive = destination / "python.zip"
                download("https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip", archive, cancel)
                extract_safe(archive, destination)
                (destination / "python311._pth").write_text("python311.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8")
                site = destination / "Lib/site-packages"
                site.mkdir(parents=True, exist_ok=True)
                with urlopen("https://pypi.org/pypi/pip/25.1.1/json", timeout=30) as response:
                    metadata = json.load(response)
                wheel = next(item for item in metadata["urls"] if item["filename"] == "pip-25.1.1-py3-none-any.whl")
                archive = destination / "pip.whl"
                download(wheel["url"], archive, cancel, wheel["digests"]["sha256"])
                extract_safe(archive, site)
            else:
                if getattr(sys, "frozen", False):
                    raise RuntimeError("This packaged Runtime supports Windows; Linux source mode needs Python 3.11")
                run_checked([sys.executable, "-m", "venv", str(destination)], cancel, log)
        if os.name == "nt" and not (destination / "Lib/site-packages/pip/__main__.py").is_file():
            site = destination / "Lib/site-packages"
            site.mkdir(parents=True, exist_ok=True)
            with urlopen("https://pypi.org/pypi/pip/25.1.1/json", timeout=30) as response:
                metadata = json.load(response)
            wheel = next(item for item in metadata["urls"] if item["filename"] == "pip-25.1.1-py3-none-any.whl")
            archive = destination / "pip.whl"
            download(wheel["url"], archive, cancel, wheel["digests"]["sha256"])
            extract_safe(archive, site)
            (destination / "python311._pth").write_text("python311.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8")
        progress("Installing pinned inference dependencies (reused on later runs)")
        index = "https://download.pytorch.org/whl/" + ("cu126" if gpu else "cpu")
        run_checked([str(interpreter), "-m", "pip", "install", "--disable-pip-version-check", "torch==2.7.1", "torchvision==0.22.1", "--index-url", index], cancel, log)
        run_checked([str(interpreter), "-m", "pip", "install", "--disable-pip-version-check", *packages], cancel, log)
        run_checked([str(interpreter), "-m", "pip", "check"], cancel, log)
        check_cancel(cancel)
        marker.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return interpreter
    finally:
        LOCK.release()
