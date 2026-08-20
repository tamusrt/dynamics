def main(eng):
    import easygui
     
    diameters = {'24' : .141,'29':.688,'38':.688,'54':1.25,'75':1.25,'98':2.05,'150':4.250}
    
    motor_name = easygui.enterbox("Enter the name of the motor, following that naming convention (manufacturer_classification): ")
    from os import getcwd
    filename = easygui.fileopenbox(msg="Select .eng file",filetypes="\*.eng",default=getcwd() +'/*.eng')

    with open(filename, 'r') as motor_file:
        lines = motor_file.readlines()
        while ";" in lines[0]:
            lines = lines[1:]
        prop_mass = float(lines[0].split()[4])
        total_mass = float(lines[0].split()[5])
        lenGrain = float(lines[0].split()[2])/25.4
        empty_mass = total_mass-prop_mass
        diameter = float(lines[0].split()[1])
        lines = lines[1:]
        #.eng file, and then can use altitude adjusted thrust curve from RAS
        splitted = []
        for line in lines:
            splitted.append(line.strip().split())
        
        
        for key in diameters.keys():
            key = int(key)
            if int(key) - 1 <= diameter <= int(key) + 1:
                exit_d = diameters[str(key)]
                
                continuation = (easygui.enterbox("Use custom motor exit diameter or estimated value of " + str(exit_d) + '? Enter custom value (inches) or enter to continue with the default: '))
                if continuation:
                    exit_d = float(continuation)
                else:
                    diameter = diameter/25.4
                
        
        
        ## plug into RAS
        easygui.msgbox('Make sure that your motor is loaded correctly into RAS and that that RASA is the last program touched by the user. Click the Flight Simulation button, alt-tab back to this page, and continue: ')
        #
        make_files(motor_name)
        print("CSV and .dat files created!\n")
        
        
        
        #run matlab code to make engines
        print('Running code to create .mat file')
        eng.create_eng(motor_name,total_mass,prop_mass,empty_mass,diameter,exit_d,lenGrain)
        print('.mat file done!')
        return motor_name,prop_mass,total_mass,lenGrain,empty_mass,diameter


def make_dat(motor_name):
    data = []

    with open(f'{motor_name}/Flight Test.CSV') as file:
        import numpy as np
        data = np.genfromtxt(file,delimiter=',')
        data = np.delete(data, [i for i in range(1,7)] + [i for i in range(9,24)] ,1)
        data = data[1:]
        final_mass = data[-1][2]
        
    #final_mass = data()
    with open(f'{motor_name}/{motor_name}.dat','w') as datfile:
        datfile.write('Time (sec)   Weight (lb)	Thrust (lb)\n')
        for row in data:
            datfile.write(f'{row[0]:.2f}\t{(row[2]-final_mass):.2f}\t{row[1]}\n')

def make_files(motor_name):
    import pyautogui as pag
    import time
    import os
    pag.PAUSE = .3
    
    #navigate to flight simulation graphs

    
    pag.typewrite(['tab'] * 7)
    pag.typewrite(['space'])
    time.sleep(1)
    
    #open file-saving screen
    pag.typewrite(['alt','\n','\n','\n','tab','\n'])
    time.sleep(1)
    #set custom save directory
    pag.typewrite(['tab'])
    pag.typewrite(['tab'])
    pag.typewrite(['tab'])
    pag.typewrite(['tab'])
    pag.typewrite(['tab'])
    pag.typewrite(['tab'])
    pag.typewrite(['enter'])
    
    os.mkdir(f'{motor_name}')
    pag.typewrite(['backspace'])
    pag.typewrite(os.getcwd()+f'\\{motor_name}')
    pag.typewrite(['enter'])
    pag.hotkey('alt','s')
    
    #overrides duplicates in the folder
    pag.typewrite(['tab','enter'])
    pag.hotkey('alt','tab')
    
    make_dat(motor_name)

