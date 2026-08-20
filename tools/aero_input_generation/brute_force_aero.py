# Developed: Luke Adams no date
# Updated: Sarah Kinney SRT-8 04/02/2021
# Updated: Nacho Durante SRT-11 11/30/2023
# Notes: - New users need to update aeropath (line 21) and nozzel diameter (line 9)
#        - Before running this program, make sure RASAero II is the last program to be "touched" by the user. The script uses "alt + tab" to switch from your console to RASAero
#          which only works if RASAero was the last used program
#        - This script can be run in an IDE or console (command prompt) but console is preferred so IDE hotkeys do not interfere
#####################################################################################################################################################################################

import pyautogui
import time

nzl_dia =  1.2 # [in]
# alt-tab to rasaero
pyautogui.hotkey('alt', 'tab')
# select Tools -> Run test`
pyautogui.hotkey('alt', 't')
pyautogui.typewrite('enter')
time.sleep(0.25)
# set pause in between keys to a small value
pyautogui.PAUSE = .01
# Begin aerodata dump
for alpha in range(0, 31):
    # enter dump path
    # aero_path = "C:/Users/skinn/Documents/SRT/RASAero Input Update/Luke Brute Force/lzrs_export/alpha{}.txt".format(alpha)
    aero_path = "C:/Users/skinn/Documents/3_SRT/130W AERO Input/alpha{}.txt".format(alpha)
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.typewrite(aero_path)

    # set alpha
    pyautogui.typewrite(['tab', 'tab', 'tab', 'tab'])
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.typewrite(str(alpha))                 

    # set nozzle diameter [in]
    pyautogui.typewrite(['tab'])
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.typewrite(str(nzl_dia))

    # run test
    pyautogui.typewrite(['tab', 'tab', '\n'])
    time.sleep(0.3)

    # tab back to file select
    pyautogui.typewrite(['tab', 'tab', 'tab', 'tab'])
    print(f"Finished {alpha}")
