# Creates a .inp file for use in SRT FS, using a .CDX1 file
# Requires the matlab (python -m pip install matlabengine) and easygui modules
# dev: 12/3/2023 Nacho Durante

print("Doing basic imports (may take a few seconds)...")
import easygui, csv, os
from math import pi
import prp, brute_force_aero, aero_post
import matlab.engine
eng = matlab.engine.start_matlab()


sim_facts = easygui.multenterbox("Base simulation details","",['Filename (no extension)','Number of Monte Carlo sims', 'Number of threads', 'Random seed value'],['','','','12'])
inp_filename = sim_facts[0]
numMC = int(sim_facts[1])
num_cores = int(sim_facts[2])
seed = int(sim_facts[3])



locations_doc = 'locations.csv'
locations = {}

################# READ STORED LOCATION DATA #############
with open(locations_doc, 'r') as locs:
    reader = csv.reader(locs)
    first = True
    for line in reader:
        if first:
            first = False
            continue
        locations[line[0]] = {"elevation" : float(line[1]), "latitude" : float(line[2]), "METAR": line[3]}


locs = [key[0].upper() + key[1:] for key in locations.keys()] 

loc = easygui.choicebox("Launch Location", "", locs + ['Other']).lower()

if loc == "other":
    loc_data = easygui.multenterbox("Enter this new location's data","",["Name","Elevation","Latitude","Nearest METAR Station"])
    name = loc_data[0]
    
    elev = float(loc_data[1])
    lat = float(loc_data[2])
    metar = loc_data[3]
    locations[loc] = {"elevation" : elev, "latitude" : lat,"metar":metar}
    with open(locations_doc, 'a') as f:
        f.write(f'{loc},{elev},{lat},{metar}\n')
# ###################### GET WEATHER DATA #####################
# ### EVENTUAL TODO: use weather API for predictions/historic data

weather_type = easygui.indexbox("Pull current live weather from METAR?","")
if weather_type == 1:
    weather = easygui.multenterbox("Enter the simulation weather parameters",'',['Lower temperature bound (F)','Upper temperature bound (F)','Lower humidity bound (0 - 1)','Upper humidity bound (0 - 1)',
                                                                                 'Lower ambient pressure bound (psi)', 'Upper ambient pressure bound (psi)', 'Mean wind velocity (ft/s)', 'Standard deviation for wind velocity (ft/s)',
                                                                                 'Mean wind direction (degrees CCW from West)', 'Standard deviation for wind direction (degrees CCW from West)'])
    elev = locations[loc]['elevation']
    lat = locations[loc]['latitude']

    temps = weather[0:2]
    min_temp = float(temps[0])
    max_temp = float(temps[1])

    hums = weather[2:4]
    min_hum = float(hums[0])
    max_hum = float(hums[1])

    pressures = weather[4:6]
    min_press = float(pressures[0])
    max_press = float(pressures[1])

    winds = weather[6:8]
    mean_wind = float(winds[0])
    stdev_wind = float(winds[1])

    wind_dirs = weather[8:]
    mean_wind_dir = float(wind_dirs[0])
    stdev_wind_dir = float(wind_dirs[1])


# ################### PARSE CDX1 FILE #########################

print('Select CDX1 file in the menu that opened. It may have opened behind everything else, so shift-alt-tab to get there')

filename = easygui.fileopenbox(msg="Select .CDX1 file",filetypes="\*.CDXI",default=os.getcwd() +'/*.CDX1')

# #use eaygui for easy multi entry



with open(filename, 'r') as file:
    text = file.read()
    def get_item(identifier):
        return (text[ text.index('<' + identifier + '>') + len(identifier)  + 2: text.index('</' + identifier + '>')])
    ############ bod CLASS DATA ##################
    body_data = easygui.multenterbox('Body data','',['Are you using elliptical or non-trapezoidal fins? If so, enter their area (in^2), or 0 if not',
                                                     'Length of the rocket (in)',
                                                        'Dry mass (no motor) of the rocket (lb)',
                                                        'X (fore) direction moment of inertia (lb_f * ft * s^2)',
                                                        'Y direction moment of inertia (lb_f * ft * s^2)',
                                                        'Z direction moment of inertia (lb_f * ft * s^2)',
                                                        'Position of the centroid of the fuel grain, measured from the nose (in)',
                                                        'Position of the centroid of the fins, measured from the nose (in)',
                                                        'Launch rail length (ft)',
                                                        'Droge chute coefficient of drag (0 if n/a)',
                                                        'Main chute coefficient of drag'])
    numFin = int(get_item('Count'))
    refArea = (float(get_item('Diameter'))/2) **2 * pi
    fin_area = float(body_data[0])
    if fin_area == 0:
            
        root_chord = float(get_item('Chord'))
        tip_chord = float(get_item('TipChord'))
        span = float(get_item('Span'))
        fin_area = 1/2 * (root_chord + tip_chord) * span
    mass = float(body_data[2])
    length = float(body_data[1])
    ixx_dry = float(body_data[3])
    iyy_dry = float(body_data[4])
    izz_dry = float(body_data[5])
    posFuel = float(body_data[6])
    posFins =float( body_data[7])
    railLength = float(body_data[8])
    cd_drogue =float( body_data[9])
    cd_main = float(body_data[10])
    


    center_grav = float(get_item('SustainerCG'))
    aero_name = easygui.enterbox('Do you have an aero input file? Enter the filename (.dat preferred) here, or leave blank to enter aero input creation: ')
    if aero_name:
        aero_name = aero_name
    
    else:
        diameter = easygui.enterbox('Entering aero input file creation.\nMake sure that RASAero is open and that it is the last "touched" file.\nFinally, make sure your rocket model is loaded correctly\nand that RAS is on the main screen.\nEnter your nozzle diameter (inches) to continue.')
        diameter = float(diameter)
        
        
        
    #     #make a new aero file every time, so progress isn't lost
        file_index = 0
        try:
            os.mkdir(f'aero_files{file_index}')
        except:
            while True:
                
                try:
                    os.mkdir(f'aero_files{file_index}')
                    break
                except:
                    file_index += 1
                        
                
                
        brute_force_aero.create(diameter, os.getcwd().replace('\\','/')+f'/aero_files{file_index}')
        
        print('Aero files successfully created in aero_files'+str(file_index) + ' folder.')
        aero_name = easygui.enterbox("Enter the name for the aero file (no file extension): ") + '.dat'
        print('Generating .dat aero file')
        aero_post.make_aero(file_index,aero_name, eng)
        delete_check = easygui.enterbox("Delete the 31 alpha files? The .dat file has been created, so there is no need for them (y/n): ")
        if delete_check == 'y':
            import shutil
            shutil.rmtree('aero_files'+str(file_index))
        
    made_prop = False
    props = (os.listdir('../../input/prp'))
    props.remove("Template and old data")
    prop = easygui.choicebox('Here are the currently stored motor files.\nSelect the one your rocket has, or choose "Other" to create a new one','',props +['Other'])
    
    prop_mass,total_mass,lenGrain,empty_mass,diameter = 0,0,0,0,0
    
    if prop.lower() == 'other':
        made_prop = True
        #makes all of the prop stuff, and also returns the motorname for later use
        motor_name,prop_mass,total_mass,lenGrain,empty_mass,diameter = prp.main(eng)
        
    else:
        motor_name = prop


#move files to correct locations
if made_prop:
    os.rename(motor_name,"../../input/prp/"+motor_name)
os.rename(aero_name, "../../input/aero/"+aero_name)





with open(inp_filename+'.inp','w') as file:
    file.write(f'-------------------------------\n')
    file.write(f'//Simulation Setup\n')
    file.write(f'sim\n')
    file.write('{\n')
    file.write(f'    name = "{inp_filename}";\n')
    file.write(f'\n')
    file.write(f'    // Time step\n')
    file.write(f'    timeStep = 0.01;\n')
    file.write(f'\n')
    file.write(f'    // Simulation time\n')
    file.write(f'   timeFlight = 48;\n')
    file.write(f'\n')
    file.write(f'    // Number of Monte Carlo flights\n')
    file.write(f'   numMC = {numMC};\n')
    file.write(f'    randRepeat = 1;\n')
    file.write(f'\n')
    file.write(f'    // Random seed control\n')
    file.write(f'    randSeed = 12;\n')
    file.write(f'    NumThreads = {num_cores};\n')
    file.write('}\n')
    file.write(f'-------------------------------\n')
    file.write(f'//Flight Variables\n')
    file.write(f'//Available values:\n')
    file.write(f'//time,alt,vel,accel,mach,Cd,drag,\n')
    file.write(f'//temp,grav,press,rho,spSd,thrust,mass\n')
    file.write(f'flt\n')
    file.write('{\n')
    file.write(f'    thrust\n')
    file.write('    {\n')
    file.write(f'        dist = "Half Wiener";\n')
    file.write(f'        mean = 0;\n')
    file.write(f'        stdDev = 0.025;\n')
    file.write('    }\n')
    file.write(f'    temp\n')
    file.write('    {\n')
    file.write(f'        dist = "Wiener";\n')
    file.write(f'        mean = 0;\n')
    file.write(f'        stdDev = 0.0125;\n')
    file.write('    }\n')
    file.write(f'    press\n')
    file.write('    {\n')
    file.write(f'        dist = "Wiener";\n')
    file.write(f'        mean = 0;\n')
    file.write(f'        stdDev = 0.0125;\n')
    file.write('    }\n')
    file.write(f'    rho\n')
    file.write('    {\n')
    file.write(f'        dist = "Wiener";\n')
    file.write(f'        mean = 0;\n')
    file.write(f'        stdDev = 0.0125;\n')
    file.write('    }\n')
    file.write('}\n')
    file.write(f'-------------------------------\n')
    file.write(f'//Atmospheric Properties\n')
    file.write(f'atm\n')
    file.write('{\n')
    file.write(f'    // Atmosphere modeled up to maxAlt [ft]\n')
    file.write(f'    maxAlt = {30000};\n')
    file.write(f'\n')
    file.write(f'    // [ft]\n')
    file.write(f'    elev = {elev};\n')
    file.write(f'    lat = {lat};\n')
    file.write(f'\n')
    file.write(f'    // Ambient temperature [deg F]\n')
    file.write(f'    tempAmb\n')
    file.write('    {\n')
    file.write(f'        dist = "LHS";\n')
    file.write(f'        upper = {max_temp};\n')
    file.write(f'       lower = {min_temp};\n')
    file.write('    }\n')
    file.write(f'\n')
    file.write(f'    // Ambient pressure (not sea level) [psi]\n')
    file.write(f'    pressAmb\n')
    file.write('    {\n')
    file.write(f'        dist = "LHS";\n')
    file.write(f'           upper = {max_press};\n')
    file.write(f'           lower = {min_press};\n')
    file.write('    }\n')
    file.write(f'\n')
    file.write(f'    // Ambient humidity [-], 0 <= hum <= 1\n')
    file.write(f'    relHum\n')
    file.write('    {\n')
    file.write(f'        dist = "LHS";\n')
    file.write(f'        upper = {max_hum};\n')
    file.write(f'        lower = {min_hum};\n')
    file.write('    }\n')
    file.write(f'\n')
    file.write(f'    // Average wind velocity [ft/s]\n')
    file.write(f'    windAvg\n')
    file.write('    {\n')
    file.write(f'        dist = "Normal";\n')
    file.write(f'        mean = {mean_wind};\n')
    file.write(f'        stdDev = {stdev_wind};\n')
    file.write('    }\n')
    file.write(f'\n')
    file.write(f'    // Turbulent intensity, values ~(0-4) [-]\n')
    file.write(f'    turbInt = 2;\n')
    file.write(f'\n')
    file.write(f'    // Incoming wind direction measured CCW from east [deg]\n')
    file.write(f'    windHead\n')
    file.write('    {\n')
    file.write(f'        dist = "Normal";\n')
    file.write(f'        mean = {mean_wind_dir};\n')
    file.write(f'        stdDev = {stdev_wind_dir};\n')
    file.write('    }\n')
    file.write('}\n')
    file.write(f'-------------------------------\n')
    file.write(f'//Body (Vehicle) Properties\n')
    file.write(f'bod\n')
    file.write('{\n')
    file.write(f'    // [-]\n')
    file.write(f'    numFin = {numFin};\n')
    file.write(f'\n')
    file.write(f'    // [ft^2] reference (cross-sectional) area\n')
    file.write(f'    refArea =  {refArea}/ 144;\n')
    file.write(f'\n')
    file.write(f'    // [ft^2]\n')
    file.write(f'    finArea =  {fin_area} / 144;\n')
    file.write(f'    \n')
    file.write(f'    // measured mass on scales [lb_m]\n')
    file.write(f'    massDry = {mass};\n')
    file.write(f'\n')
    file.write(f'    // [ft]\n')
    file.write(f'    length = {length} / 12;\n')
    file.write(f'   ixxDry = {ixx_dry};\n')
    file.write(f'\n')
    file.write(f'    // [lb_f * ft * s^2]\n')
    file.write(f'    iyyDry = {iyy_dry};\n')
    file.write(f'\n')
    file.write(f'    // [lb_f * ft * s^2]\n')
    file.write(f'    izzDry = {izz_dry};\n')
    file.write(f'    posCG =  {center_grav}/ 12;\n')
    file.write(f'\n')
    file.write(f'    // Centroid of fuel grain measured from nose [ft]\n')
    file.write(f'    posFuel =  {posFuel}/ 12;\n')
    file.write(f'\n')
    file.write(f'    // Centroid of fins measured from nose [ft]\n')
    file.write(f'    posFin = {posFins} / 12;\n')
    file.write(f'\n')
    file.write(f'    // Launch rail length [ft]\n')
    file.write(f'    railLength = {railLength};\n')
    file.write(f'\n')
    file.write(f'    // Launch rail elevation from horizontal [deg]\n')
    file.write(f'    railElev\n')
    file.write('    {\n')
    file.write(f'        dist = "Normal";\n')
    file.write(f'        mean = 90;\n')
    file.write(f'        stdDev = 0.5;\n')
    file.write('    }\n')
    file.write(f'\n')
    file.write(f'    // Launch rail heading measured CCW from east [deg]\n')
    file.write(f'    railHead\n')
    file.write('    {\n')
    file.write(f'        dist = "Normal";\n')
    file.write(f'        mean = 90;\n')
    file.write(f'        stdDev = 1;\n')
    file.write('    }\n')
    file.write(f'\n')
    file.write(f'    // Aluminum to Aluminum (dry) dynamic coefficient of friction [-]\n')
    file.write(f'    railFrict = 0.5;\n')
    file.write(f'\n')
    file.write(f'    // Filepath to aerodynamic dataset\n')
    file.write(f'    aeroModel = "./input/aero/{aero_name}";\n')
    file.write('}\n')
    file.write(f'-------------------------------\n')
    file.write(f'//Propulsion Properties\n')
    file.write(f'prp\n')
    file.write('{\n')
    file.write(f'    // Solid or hybrid?\n')
    file.write(f'    type = "solid";\n')
    file.write(f'    \n')
    file.write(f'    // HEM filepath\n')
    file.write(f'    engModel = "input/prp/{motor_name}/{motor_name}.mat";\n')
    file.write(f'    timeStep = 0.01;\n')
    file.write(f'    engine = "{motor_name}";\n')
    
    file.write(f'\n')
    file.write(f'    // |logical|\n')
    file.write(f'   additives = false;\n')
    file.write(f'    interp = "makima";\n')
    file.write(f'    extrap = "makima";\n')
    file.write(f'\n')
    file.write(f'\n')
    file.write(f'\n')
    file.write(f'    // Thrust efficiency, multiplicative factor [-]\n')
    file.write(f'    thrustEta = 1;\n')
    file.write(f'\n')
    file.write(f'    // Engine startup time [s]\n')
    file.write(f'    startup\n')
    file.write('    {\n')
    file.write(f'        dist = "LHS";\n')
    file.write(f'        lower = .1;\n')
    file.write(f'        upper = .1;\n')
    file.write('    }\n')
    file.write(f'    forceOptimExp = true;\n')
    file.write(f'    runVentCalc = false;\n')
    file.write('}\n')
    file.write(f'\n')
    file.write(f'imp\n')
    file.write('{\n')
    file.write(f'    // Simulate recovery?\n')
    file.write(f'    runRecov = true;\n')
    file.write(f'    timeStep = 0.01;\n')
    file.write(f'   timeRecov = 300;\n')
    file.write(f'\n')
    file.write(f'    // Main deployment altitude\n')
    file.write(f'    mainAlt = 1000;\n')
    file.write('    {\n')
    file.write(f'        dist = "Uniform";\n')
    file.write(f'        lower = 900;\n')
    file.write(f'        upper = 1100;\n')
    file.write('    }\n')
    file.write(f'\n')
    file.write(f'    // Coefficient of drag, drogue\n')
    file.write(f'    cdsDrogue = {cd_drogue};\n')
    file.write(f'\n')
    file.write(f'    // Coefficient of drag, main\n')
    file.write(f'    cdsMain = {cd_main};\n')
    file.write(f'\n')
    file.write(f'    // Coefficient of drag, vehicle (lateral,free stream perpendiular to rocket) = 1.1 (thin disk) * pi * length^2 / 4 / 144\n')
    file.write(f'    cdsLat = 1.1 * ({length} / 12) * ({diameter} / 12);\n')
    file.write(f'    // Coefficient of drag, vehicle (longitudinal,free stream parallel to rocket)\n')
    file.write(f'    cdsLong = 0.82 * (({diameter**2}) * pi / 4 / 144);\n')
    file.write('}\n')
    file.write(f'\n')
    
    
os.rename(inp_filename, "../../input/"+inp_filename)
