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

# Commit messages
Start every commit message with the project it changes, then a colon:

```
NIMITZ: moved the fins 0.125 in aft
tools: fix the stage toggle on the Vehicles page
AIRMAIL, tools: new thrust curve and its sim config
```

The projects are the folders in `aero_modeling/` (one per vehicle) and the other top-level folders (`tools`,
`datcom`, `design`, ...), so a new vehicle folder is a project as soon as it is committed. Case does not matter.

Turn on the check once per clone; it then refuses a commit without a project and lists them:

```
git config core.hooksPath .githooks
```

Commits that skip it (made on github.com, or with `--no-verify`) are flagged after the push by the
**Commit messages** workflow, which fails with a red X on the commit. Nothing else waits on it.

# In-progress tasks
- flight coefficient reconstruction and comparison with RAS, Datcom
- Tools to interface with DATCOM for FS purposes
- Many more!
