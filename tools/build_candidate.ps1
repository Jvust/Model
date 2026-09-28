# Run in a clean Windows build environment with Python 3.11.
$ErrorActionPreference='Stop'
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Source tests failed.' }
python -m pip install --disable-pip-version-check pyinstaller py7zr
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed.' }
python -m PyInstaller --onefile --name ModelRuntime --paths runtime --collect-all py7zr --add-data 'runtime\workflows;workflows' --add-data 'runtime\native_worker.py;.' --add-data 'runtime\flux_edit_worker.py;.' --add-data 'index.html;site' --add-data 'sw.js;site' --add-data 'assets;site/assets' --add-data 'docs;site/docs' --add-data 'notebooks;site/notebooks' runtime\application.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Copy-Item -Force dist\ModelRuntime.exe runtime\ModelRuntime.exe
python -m tools.smoke_candidate_exe
if ($LASTEXITCODE -ne 0) { throw 'Built EXE smoke check failed.' }
