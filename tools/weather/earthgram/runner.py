"""Run GRAM2016.exe once in its own temporary folder and return the parsed tables."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from . import config
from .namelist import GramInputs, dir_arg, render
from .parse import read_special

# GRAM prints this when it can't open an input file; for some data files (NCEP,
# RRA, topography) it then carries on with missing data instead of exiting.
_OPEN_ERROR = re.compile(r"File Open Error!\s*(.*)")


class GramError(RuntimeError):
    pass


def run(inp: GramInputs, *, keep_dir: Path | None = None,
        timeout_s: float = 600) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run GRAM for `inp` and return (mean, runs) as parse.read_special does.

    With `keep_dir`, the namelist and raw outputs are copied there.
    """
    home = config.check_install()
    text = render(inp, home)
    work = Path(tempfile.mkdtemp(prefix="gram_"))
    try:
        (work / "namelist.txt").write_text(text)
        # GRAM prompts for the folder and the file name on stdin. It calls system("pause")
        # at the end of every run and before exit(1) on every input error; the pause
        # returns at once because stdin is exhausted, so errors show up as a non-zero exit.
        proc = subprocess.run(
            [str(config.gram_exe(home))],
            input=f"{dir_arg(work)}\nnamelist.txt\n",
            cwd=work, capture_output=True, text=True, timeout=timeout_s,
        )
        out = proc.stdout + proc.stderr
        unopened = _OPEN_ERROR.findall(out)
        if unopened:
            raise GramError(f"GRAM couldn't open: {', '.join(p.strip() for p in unopened)}")
        special = work / "special.txt"
        if proc.returncode != 0 or not special.is_file():
            raise GramError(f"GRAM failed (exit {proc.returncode}):\n{out.strip()[-2000:]}")
        mean, runs = read_special(special, inp.nmax)
        if len(runs) != max(inp.mc, 1) * inp.nmax:
            raise GramError(f"GRAM wrote {len(runs)} rows, expected {max(inp.mc, 1) * inp.nmax}")
        if keep_dir:
            keep_dir = Path(keep_dir)
            keep_dir.mkdir(parents=True, exist_ok=True)
            for f in work.iterdir():
                shutil.copy2(f, keep_dir / f.name)
            (keep_dir / "stdout.txt").write_text(out)
        return mean, runs
    finally:
        shutil.rmtree(work, ignore_errors=True)
