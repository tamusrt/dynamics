# dynamics
This repo will house dynamics toolsets for rocket design and analysis

# Setup
Requires Python 3.11+ and a virtual environment.

```
git clone git@github.com:tamusrt/dynamics.git
cd dynamics
python -m venv .venv
```

Activate it, then install the dependencies:

```
.\.venv\Scripts\Activate.ps1    # Windows (PowerShell)
source .venv/bin/activate        # macOS / Linux
pip install -r requirements.txt
```

Reactivate the venv in each new shell before running any of the tools. Note that
some RASAero and DATCOM wrappers drive their GUIs through `pyautogui`, so those
tools are Windows-only. The scripts in `tools/afp/` are MATLAB and do not use
this environment.

# In-progress tasks
- flight coefficient reconstruction and comparison with RAS, Datcom
- Tools to interface with DATCOM for FS purposes
- Many more!
