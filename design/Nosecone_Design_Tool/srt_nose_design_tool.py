
import matplotlib.pyplot as plt     # Visualizing how different flights performed
import pandas as pd                 # Data analysis of flight csv's
import numpy as np                  # Generating and dealing with arrays
import pyautogui                    # Automation of computer tasks with Python
import json                         # Parsing through the input file
import time                         # Adding time gaps for processes to load


class Nose():
    def __init__(self, shape:str, diameter:float, power_law:float, 
                 length:float, simulation_number: int) -> None:
        self.shape = shape
        self.diameter = diameter
        self.power_law = power_law
        self.length = length
        self.simulation_number = simulation_number

    
    def edit_rocket_file(self, rocket_file) -> None:
        """
        Edits the CDX1 file to adjust the nose cone to the next nose design to be simulated.
        """
        with open(rocket_file, 'r+') as f:
            file_lines = f.readlines()
            nose_line = 0
            for i in range(len(file_lines)):
                if file_lines[i].strip() == "<NoseCone>":
                    nose_line = i
                    break
            file_lines[nose_line+1] = f"      <PartType>NoseCone</PartType>\n"
            file_lines[nose_line+2] = f"      <Length>{self.length}</Length>\n"
            file_lines[nose_line+3] = f"      <Diameter>{self.diameter}</Diameter>\n"
            file_lines[nose_line+4] = f"      <Shape>{self.shape}</Shape>\n"
            file_lines[nose_line+5] = f"      <BluntRadius>0</BluntRadius>\n"
            file_lines[nose_line+6] = f"      <Location>0</Location>\n"
            file_lines[nose_line+7] = f"      <Color>Black</Color>\n"
            if (self.power_law != "None") and (self.shape == "Power Law"):
                file_lines[nose_line+8] = f"      <PowerLaw>{self.power_law}</PowerLaw>\n"
            if (self.shape != "Power Law") and ("<PowerLaw>" in file_lines[nose_line+8]):         
                print("OOPS")

        with open(rocket_file, "w") as f:
            for line in file_lines:
                f.write(line)


    def open_rocket_file(self, rocket_file) -> None:
        """
        Opens the CDX1 in RASAero.
        """
        # Opens the open file menu
        pyautogui.hotkey('alt', 'f')
        pyautogui.press('down')
        pyautogui.press('enter')

        # Declines saving changes
        pyautogui.press('tab')
        pyautogui.press('enter')

        pyautogui.write(rocket_file)
        time.sleep(0.25)
        pyautogui.press('enter')
        time.sleep(1)


    def simulate(self) -> None:
        # Locate and click flight simulation button
        flight_sim_button = pyautogui.locateCenterOnScreen("flight_simulation.png", grayscale=True, confidence=0.7)
        pyautogui.moveTo(flight_sim_button)
        pyautogui.leftClick()

        # View flight data
        pyautogui.press('tab', presses=7)
        pyautogui.press('space')

        # Check if a new window pops up, this means the view data has finished loading
        times_slept = 0 # Fail safe incase flight never loads, the loop will exit if the program has waited long enough.
        while (pyautogui.locateCenterOnScreen('flight_loaded.png', grayscale=True, confidence=0.7) == None) and (times_slept < 100):
            if stability_warning():
                pyautogui.hotkey('alt', 'f4')
                break
            time.sleep(1)
            times_slept += 1


    def save_flight_data(self) -> None:
        # Press file -> export -> to csv -> 0.01s -> done
        pyautogui.hotkey('alt', 'f')
        pyautogui.press('enter', presses=2)
        pyautogui.press('tab')
        pyautogui.press('enter')

        # Write file name to be saved
        time.sleep(0.25)
        filename = f"nose_{self.simulation_number}"
        pyautogui.write(filename)

        # Save file in output directory
        pyautogui.hotkey('ctrl', 'l')
        pyautogui.press('right')
        pyautogui.write('\Output')
        pyautogui.press('enter')
        pyautogui.hotkey('alt', 'b', 'n')
        pyautogui.press('enter')

        # Close windows
        pyautogui.hotkey('alt', 'f4')
        pyautogui.hotkey('alt', 'f4')
        pyautogui.hotkey('tab', 'space')


def process_inputs(filename) -> np.array:
    """
    Opens the json input file and returns an array of each variation of noses to be simulated.
    """
    with open(filename, 'r') as file:
        inputs = json.load(file)

        # Extract design parameters from json file
        diameter = inputs['diameter']
        power_laws = inputs['power_laws']
        shapes = inputs['shapes']
        length_params = inputs['length']

        # Generate lengths array depending on if user specified a step or number
        if length_params['number'] == "None":
            lengths = np.arange(length_params['minimum'], length_params['maximum']+length_params['step'], length_params['step'])
        else:
            lengths = np.linspace(length_params['minimum'], length_params['maximum'], length_params['number'])

        # Initialize array of options
        nose_designs = np.array([])
        sim_num = 1

        # Generate each possible combination of design parameters and append to nose_designs array
        for shape in shapes:
            for power_law in power_laws:
                for length in lengths:
                    nose_designs = np.append(nose_designs, Nose(shape, diameter, power_law, length, sim_num))
                    sim_num += 1

    return nose_designs


def stability_warning() -> bool:
    """
    Determines if a stability warning pops up during flight simulation.
    """
    stability_warning_button = pyautogui.locateCenterOnScreen("stability_warning.png", grayscale=True, confidence=0.7)
    # Determine whether the locate function found the stability warning on the screen
    if stability_warning_button != None:
        return True
    else:
        return False


if __name__ == "__main__":
    input_file = "test.json"
    rocket_file = "Odysseus.CDX1"
    engine_file = "SRT_207.eng"

    pyautogui.PAUSE = 0.2  # This value is the amount of time pyautogui waits between executing commands

    nose_options = process_inputs(input_file)

    pyautogui.hotkey('alt', 'tab')

    for nose in nose_options:
        nose.edit_rocket_file(rocket_file)
        nose.open_rocket_file(rocket_file)
        nose.simulate()
        nose.save_flight_data()
