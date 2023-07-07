"""
Written by Trevor Spielman
SRT-X Dynamics 11/20/2022

"edit_file" function written by Sarah Kinney SRT-X Dynamics

Generates and simulates different variations of fin geometries in RASAero and saves CSV's of each sim
"""

import pyautogui as pag
import time
import numpy as np


def populate():
    """
    Creates and populates an array of "points" for fin simulations. Each point represents
    the paramers for one simulation in RASAero.
    """
    fin_cases = []

    # Generates a set of roots and spans that the other parameters depend on
    spans = [6]
    roots = [15]

    # Constant for each test
    thick = 0.5
    airfoil = "Double Wedge"
    sim = 1

    # Generate each possible combination of parameters
    for span in spans:
        for root in roots:
            tips = [1/3*root]
            dist = root + 0.5
            for tip in tips:
                chamfs = [(tip+root)/4]
                sweeps = [(root-tip)*(2/3), (root-tip)]
                for sweep in sweeps:
                    for chamf in chamfs:
                        fin_cases.append((span, root, tip, sweep, thick, dist, airfoil, chamf))
                        print(f"file_{sim} - Span:{span} Root:{root} Tip:{tip} Sweep:{sweep}, Chamf:{chamf}")
                        sim += 1

    return fin_cases


def save(simNum):
    filename = "fin_" + str(simNum)

    pag.hotkey('alt', 'f')
    pag.press('enter', presses=2)
    pag.hotkey('tab', 'enter')

    pag.hotkey('alt', 'd')
    pag.typewrite(path)
    time.sleep(0.25)
    pag.press('enter')

    pag.hotkey('alt', 'b')
    pag.hotkey('alt', 'n')
    pag.typewrite(filename)
    time.sleep(0.25)
    pag.press('enter')
    time.sleep(1)

    pag.hotkey('alt', 'f4')
    pag.hotkey('alt', 'f4')
    pag.press('enter')


def edit_file(rocket_file, fin_cases, sim_num):

    with open(rocket_file, 'r+') as f:
        file_lines = f.readlines()
        fin_line = 0
        for i in range(len(file_lines)):
            if file_lines[i].strip() == "<Fin>":
                fin_line = i
                break
        file_lines[fin_line+1] = f"        <Count>{3}</Count>\n"
        file_lines[fin_line+2] = f"        <Chord>{fin_cases[sim_num-1][1]}</Chord>\n"
        file_lines[fin_line+3] = f"        <Span>{fin_cases[sim_num-1][0]}</Span>\n"
        file_lines[fin_line+4] = f"        <SweepDistance>{fin_cases[sim_num-1][3]}</SweepDistance>\n"
        file_lines[fin_line+5] = f"        <TipChord>{fin_cases[sim_num-1][2]}</TipChord>\n"
        file_lines[fin_line+6] = f"        <Thickness>{fin_cases[sim_num-1][4]}</Thickness>\n"
        file_lines[fin_line+7] = f"        <LERadius>{0}</LERadius>\n"
        file_lines[fin_line+8] = f"        <Location>{fin_cases[sim_num-1][5]}</Location>\n"
        file_lines[fin_line+9] = f"        <AirfoilSection>{fin_cases[sim_num-1][6]}</AirfoilSection>\n"
        file_lines[fin_line+10] = f"        <FX1>{fin_cases[sim_num-1][7]}</FX1>\n"
        file_lines[fin_line+11] = f"        <FX3>{fin_cases[sim_num-1][7]}</FX3>\n"

    with open(rocket_file, "w") as f:
        for line in file_lines:
            f.write(line)


def open_file(rocket_file):
    # Open rocket file
    pag.hotkey('ctrl', 'o')
    pag.hotkey('tab', 'enter')

    pag.hotkey('alt', 'd')
    pag.typewrite(path)
    time.sleep(0.25)
    pag.press('enter')

    pag.hotkey('alt', 'n')
    pag.typewrite(rocket_file)
    time.sleep(0.25)
    pag.press('enter')


def sim(sim_num):
    edit_file(rocket_name, fin_cases, sim_num) 
    open_file(rocket_name)

    pag.moveTo(960, 110)
    pag.click()
    time.sleep(1)                          
    
    pag.press('tab', presses=7)
    pag.press('space')
    time.sleep(1)

    save(sim_num)

if __name__ == '__main__':
    pag.PAUSE = 0.25
    
    rocket_name = "Odysseus.CDX1"
    engine_name = "SRT_207.eng"
    path = r"C:\Users\trevo\Documents\SRT\SRTX\Fins"
    sim_num = 1
    
    fin_cases = populate()
    print(len(fin_cases))

    pag.hotkey('alt', 'tab')

    # Open engine file
    pag.hotkey('alt', 'f')
    pag.press('tab', presses=4)
    pag.press('enter')
    pag.hotkey('alt', 'd')
    pag.typewrite(path)
    time.sleep(0.25)
    pag.press('enter')

    pag.hotkey('alt', 'n')
    pag.typewrite(engine_name)
    time.sleep(0.25)
    pag.press('enter')

    for _ in range(len(fin_cases)):
        sim(sim_num)
        sim_num += 1
        time.sleep(1)
