# CFD_Tool: A tool used to calculate parameters for CFD physics and meshing settings.
# Last Modification: Eric Swinny
# Last Modified: 19 March 2021


# Tool 1 calculates physics parameters for the code using an ideal gas model of air and standard atmosphere conditions.
# Using a user input mach number, altitude, and flow angles the calculator uses the angles and standard atmosphere to
# get the necessary physics conditions needed to input for CFD in a standard test vehicle framework
# X- Through nose, Z- Up, Y- Right

# Tool 2 calculates the free stream conditions using a user input Reynolds number per unit length, Mach number, and
# temperature. This module uses Sutherland's law and the ideal gas EOS to back out pressure and density from the input
# values and the definition of Reynolds number.

# Tool 3 calculates the boundary layer settings for a wall using modified elementary flat plate boundary layer theory
# and a Y+ value defined by the user as well as the mach number, altitude, and simulation geometry parameters

# Tool 4 this tool can be used to create the macro data input lines for CFD in STAR_CCM+. it combines the functionality
# of tools 1 and 2 with the ability to iterate over mach numbers, alpha angles of attack, and beta angles of attack
# once data is calculated it is written to a text file in the format needed by the macro


# Package import
import math
import numpy

# Gets the user selection for the tool to use
print('1: Free stream atmospheric conditions calculator')
print('2: Reynolds number free stream calculator')
print('3: Boundary layer estimation tool')
print('4: Macro settings writer')
print('Enter any other number to exit')
print('--------------------------------------------------------------------')
Prog = str(input('enter the number of the tool do you want to use:  '))


if Prog == '1':
    # Constants used in calculations
    R = 287.15
    SHR = 1.4
    Tref = 273.15
    S = 110.4
    Mu_ref = 1.716*10**-5

    # Get user input parameters
    M = float(input('What is the mach number of the free stream:  '))
    h = float(input('What is the flight altitude above sea level in meters:  '))
    Alpha = float(input('Free stream angle of attack alpha [degrees]:  '))
    Beta = float(input('Free stream sideslip angle beta [degrees]:  '))
    print('if analyzing an axisymmetric body enter zero for gamma')
    Gamma = float(input('Free stream roll angle gamma [degrees]:  '))

    # Get altitude and calculate stagnation conditions
    # constants
    C1 = -0.0076
    C2 = 0.003
    C3 = -0.0045
    C4 = 0.004
    g0 = 9.81

    # Standard atmosphere calculations
    if h <= 11000:
        T0 = 300+C1*h
        P0 = 101325*((T0/300)**(-g0/(C1*R)))

    elif 11000 < h and h <= 25000:
        T0 = 216.66
        P0 = 22615.57*math.exp(-(g0/(R*T0))*(h-11000))

    elif 25000 < h and h <= 47000:
        T0 = 216.66+C2*(h - 25000)
        P0 = 2484.17*((T0/216.66)**(-g0/(C2*R)))

    elif 47000 < h and h <= 53000:
        T0 = 282.66
        P0 = 116.93*math.exp(-(g0/(R*T0))*(h-47000))

    elif 53000 < h and h <= 79000:
        T0 = 216.66+C2*(h - 53000)
        P0 = 56.6*((T0/282.66)**(-g0/(C3*R)))

    elif 79000 < h and h <= 90000:
        T0 = 165.66
        P0 = 0.9778*math.exp(-(g0/(R*T0))*(h-79000))

    elif 90000 < h:
        T0 = 165.66+C2*(h-90000)
        P0 = 0.101*((T0/165.66)**(-g0/(C4*R)))

    # converts angles to radians
    Alpha = math.radians(Alpha)
    Beta = math.radians(Beta)
    Gamma = math.radians(Gamma)

    # Generate Physics conditions
    Tinf = T0
    Pinf = P0
    Rhoinf = Pinf/(R*Tinf)
    FSV = M*(SHR*R*Tinf)**0.5
    # Sutherlands formula to calculate viscosity
    C1 = Mu_ref*(Tref+S)/(Tref**(3/2))
    Mu = (C1*Tinf**(3/2))/(Tinf+S)
    Re = Rhoinf*FSV/Mu

    # Free Stream velocity wind to body components
    WindFrameVelocity=numpy.array([[FSV], [0], [0]])
    MatAlpha = numpy.array([[numpy.cos(Alpha), 0, numpy.sin(Alpha)],[0, 1, 0],[-1*numpy.sin(Alpha), 0, numpy.cos(Alpha)]])
    MatBeta = numpy.array([[numpy.cos(Beta), -1*numpy.sin(Beta), 0], [numpy.sin(Beta), numpy.cos(Beta), 0], [0, 0, 1]])
    MatGamma = numpy.array([[1, 0, 0], [0, numpy.cos(Gamma), -1*numpy.sin(Gamma)], [0, numpy.sin(Gamma), numpy.cos(Gamma)]])
    RotationMatrix = numpy.matmul(MatAlpha, numpy.matmul(MatBeta, MatGamma))
    VelocityComponents = numpy.matmul(numpy.transpose(RotationMatrix), WindFrameVelocity)



    # Print outputs for user
    print('_______________________RESULTS_______________________')
    print('Mach number: ', M)
    print('Free stream velocity', FSV, 'm/s')
    print('Body frame velocity components [U, V, W] ', numpy.transpose(VelocityComponents), 'm/s')
    print('Wind direction components ', numpy.transpose(VelocityComponents)/FSV)
    print('Static temperature: ', Tinf, 'K')
    print('Static pressure: ', Pinf, 'Pa')
    print('Gauge pressure: ', Pinf-P0, 'Pa')
    print('Static density', Rhoinf, 'kg*m^-3')
    print('Kinematic viscosity', Mu, 'kg/(m*s)')
    print('Reynolds number', Re, 'm^-1')


elif Prog == '2':
    # Get input conditions
    Mach = float(input('What is the free stream Mach number:  '))
    ReL = float(input('What is the Free stream Reynolds number [1/Mm]:  ')) * (10 ** 6)
    Tinf = float(input('What is the free stream temperature [K]:  '))
    Alpha = float(input('Free stream angle of attack alpha [degrees]:  '))
    Beta = float(input('Free stream sideslip angle beta [degrees]:  '))
    print('if analyzing an axisymmetric body enter zero for gamma')
    Gamma = float(input('Free stream roll angle gamma [degrees]:  '))

    # Constants
    R = 287.16  # J/(kg*K)
    SHR = 1.4
    Tref = 273.15  # K
    S = 110.4  # K
    Mu_ref = 1.716 * 10 ** -5  # kg/(m*s)

    # converts angles to radians
    Alpha = math.radians(Alpha)
    Beta = math.radians(Beta)
    Gamma = math.radians(Gamma)

    # Calculate free stream parameters
    Uinf = Mach * math.sqrt(SHR * R * Tinf)
    HalfMU = Mu_ref * ((Tinf / Tref) ** (3 / 2))
    Mu = HalfMU * ((S + Tref) / (S + Tinf))
    P0P = ((1 + ((SHR - 1) / 2) * Mach ** 2) ** (SHR / (SHR - 1)))

    # Get Pressure of free stream
    Pinf = ReL * Mu * R * Tinf / Uinf

    # Get remaining parameters
    Rhoinf = Pinf / (R * Tinf)
    P0 = P0P * Pinf

    # Free Stream velocity wind to body components
    FSV = Uinf
    WindFrameVelocity = numpy.array([[FSV], [0], [0]])
    MatAlpha = numpy.array([[numpy.cos(Alpha), 0, numpy.sin(Alpha)], [0, 1, 0], [-1*numpy.sin(Alpha), 0, numpy.cos(Alpha)]])
    MatBeta = numpy.array([[numpy.cos(Beta), -1*numpy.sin(Beta), 0], [numpy.sin(Beta), numpy.cos(Beta), 0], [0, 0, 1]])
    MatGamma = numpy.array([[1, 0, 0], [0, numpy.cos(Gamma), -1*numpy.sin(Gamma)], [0, numpy.sin(Gamma), numpy.cos(Gamma)]])
    RotationMatrix = numpy.matmul(MatAlpha, numpy.matmul(MatBeta, MatGamma))
    VelocityComponents = numpy.matmul(numpy.transpose(RotationMatrix), WindFrameVelocity)

    # output
    print('_______________________RESULTS_______________________')
    print('Mach number', Mach)
    print('Free stream velocity', Uinf, 'm/s')
    print('Static pressure', Pinf, 'Pa')
    print('Static Density', Rhoinf, 'kg/m^3')
    print('Static Temperature', Tinf, 'K')
    print('Reynolds Number', Pinf * Uinf / (Mu * R * Tinf), '1/m')
    print('Body frame velocity components [U, V, W] ', numpy.transpose(VelocityComponents), 'm/s')
    print('Wind direction components ', numpy.transpose(VelocityComponents)/FSV)


elif Prog == '3':
    # Calculates a first cell height value from a Y+ value
    # Get user input parameters
    M = float(input('What is the mach number of the free stream:  '))
    Tinf = float(input('What if the free stream temperature in Kelvin:  '))
    Pinf = float(input('What if the free stream pressure in Pascal:  '))
    LRef = float(input('What is the reference length of the vehicle in inches:  '))/(12*3.28)
    Length = float(input('What is the vehicle length in inches:  ')) / (12 * 3.28)
    Yplus = float(input('What is the Y+ desired for the first cell (typically y+ <= 1):  '))

    # Get altitude and calculate stagnation conditions
    # constants
    R = 287.15
    SHR = 1.4
    Tref = 273.15
    S = 110.4
    Mu_ref = 1.716 * 10 ** -5
    Pr = 0.72

    # Calculate Physics conditions
    Rhoinf = Pinf/(R*Tinf)
    FSV = M*(SHR*R*Tinf)**0.5
    C1 = Mu_ref*(Tref+S)/(Tref**(3/2))
    Mu = (C1*Tinf**(3/2))/(Tinf+S)
    Re = Rhoinf*FSV*LRef/Mu

    # Y+ calculations (see AIAA 2002-3191 for further details)
    f_comp = 1+0.1157*M**2
    Mu_Rat = ((1+(S/Tinf))/(f_comp+(S/Tinf)))*(f_comp)**1.5
    r_comp = 1/(Mu_Rat*f_comp)

    TwTinf = 1+((SHR-1)/2)*(Pr**(1/3))*(M**2)
    MuwMuinf = (TwTinf**(3/2))*((1+(S/Tinf))/(TwTinf+(S/Tinf)))
    RhowRhoinf = 1/TwTinf
    Cf = 0.455/(f_comp*math.log(0.06*r_comp*Re, math.exp(1))**2)

    ds = math.sqrt(2/Cf)*(MuwMuinf/math.sqrt(RhowRhoinf))*Yplus/Re

    # calculations to find approximate height of boundary layer (Very inaccurate, needs improvement)
    H = 0.16*Length*((FSV*Rhoinf*Length/Mu)**-0.1428)*3.28*12

    # output values for user
    print('_______________________RESULTS_______________________')
    print('First cell height', ds, 'meters')
    print('---------Boundary Layer height is most likely wrong---------')
    print('Boundary layer height:  ', H, 'inches')



elif Prog == '4':
    # file writer for macro
    # Get ranges to work with
    print('Free stream varying settings')
    MachMin = float(input('What is the minimum free stream mach number:  '))
    MachMax = float(input('What is the maximum free stream mach number:  '))
    MachStep = float(input('What is the step size between mach numbers:  '))
    AlphaMin = math.radians(float(input('What is the minimum alpha [deg.]:  ')))
    AlphaMax = math.radians(float(input('What is the maximum alpha [deg.]:  ')))
    AlphaStep = math.radians(float(input('What is the step size between alphas [deg.]:  ')))
    BetaMin = math.radians(float(input('What is the minimum beta [deg.]:  ')))
    BetaMax = math.radians(float(input('What is the maximum beta [deg.]:  ')))
    BetaStep = math.radians(float(input('What is the step size between betas [deg.]:  ')))
    h = float(input('What is the altitude above sea level in meters:  '))

    print('Boundary layer and vehicle geometry settings')
    Length = float(input('What is the vehicle length in inches:  '))/(3.28*12)
    Yplus = float(input('What is the Y+ number of the flow:  '))
    Sv = float(input('What is the boundary layer stretching value:  '))
    LRef = float(input('What is the reference length of the vehicle in inches:  '))/(12*3.28)

    print('Filename for writing')
    print('WARNING: Text files of the same name will be erased')
    Filename = str(input('What is the filename to write to ( do not include .txt at the end):  ')) + ".txt"

    DataFile = open(Filename, "w")

    # Constants used in calculations
    R = 287.15
    SHR = 1.4
    Tref = 273.15
    S = 110.4
    Mu_ref = 1.716 * 10 ** -5

    # Get altitude and calculate stagnation conditions
    # constants
    C1 = -0.0076
    C2 = 0.003
    C3 = -0.0045
    C4 = 0.004
    g0 = 9.81

    # Standard atmosphere calculations
    if h <= 11000:
        T0 = 300 + C1 * h
        P0 = 101325 * ((T0 / 300) ** (-g0 / (C1 * R)))

    elif 11000 < h and h <= 25000:
        T0 = 216.66
        P0 = 22615.57 * math.exp(-(g0 / (R * T0)) * (h - 11000))

    elif 25000 < h and h <= 47000:
        T0 = 216.66 + C2 * (h - 25000)
        P0 = 2484.17 * ((T0 / 216.66) ** (-g0 / (C2 * R)))

    elif 47000 < h and h <= 53000:
        T0 = 282.66
        P0 = 116.93 * math.exp(-(g0 / (R * T0)) * (h - 47000))

    elif 53000 < h and h <= 79000:
        T0 = 216.66 + C2 * (h - 53000)
        P0 = 56.6 * ((T0 / 282.66) ** (-g0 / (C3 * R)))

    elif 79000 < h and h <= 90000:
        T0 = 165.66
        P0 = 0.9778 * math.exp(-(g0 / (R * T0)) * (h - 79000))

    elif 90000 < h:
        T0 = 165.66 + C2 * (h - 90000)
        P0 = 0.101 * ((T0 / 165.66) ** (-g0 / (C4 * R)))

    # Set up mach loop
    MachIter = 0
    M = MachMin

    # Iterate over machs, alphas, and betas
    while M <= MachMax:

        # Set up alpha loop
        Alpha = AlphaMin

        while Alpha <= AlphaMax:

            # Set up beta loop
            Beta = BetaMin

            while Beta <= BetaMax:

                # calculate data to write
                Tinf = T0
                Pinf = P0
                Rhoinf = Pinf/(R*Tinf)
                FSV = M*(SHR*R*Tinf)**0.5
                Pgage = Pinf-P0

                # Sutherlands formula to calculate viscosity
                C1 = Mu_ref*(Tref+S)/(Tref**(3/2))
                Mu = (C1*Tinf**(3/2))/(Tinf+S)
                Re = Rhoinf*FSV*Length/Mu

                # Free Stream velocity wind to body components
                WindFrameVelocity = numpy.array([[FSV], [0], [0]])
                MatAlpha = numpy.array([[numpy.cos(Alpha),0,numpy.sin(Alpha)],[0,1,0],[-1*numpy.sin(Alpha),0,numpy.cos(Alpha)]])
                MatBeta = numpy.array([[numpy.cos(Beta),-1*numpy.sin(Beta),0],[numpy.sin(Beta),numpy.cos(Beta),0],[0,0,1]])
                RotationMatrix = numpy.matmul(MatAlpha,MatBeta)
                VelocityComponents = numpy.matmul(numpy.transpose(RotationMatrix), WindFrameVelocity)

                WindXComponents = numpy.matmul(RotationMatrix, numpy.array([[1], [0], [0]]))
                WindYComponents = numpy.matmul(RotationMatrix, numpy.array([[0], [1], [0]]))
                WindZComponents = numpy.matmul(RotationMatrix, numpy.array([[0], [0], [1]]))

                # Y+ calculations (see AIAA 2002-3191 for further details)
                Pr = 0.72
                f_comp = 1+0.1157*M**2
                Mu_Rat = ((1+(S/Tinf))/(f_comp+(S/Tinf)))*(f_comp)**1.5
                r_comp = 1/(Mu_Rat*f_comp)

                TwTinf = 1+((SHR-1)/2)*(Pr**(1/3))*(M**2)
                MuwMuinf = (TwTinf**(3/2))*((1+(S/Tinf))/(TwTinf+(S/Tinf)))
                RhowRhoinf = 1/TwTinf
                Cf = 0.455/(f_comp*math.log(0.06*r_comp*Re,math.exp(1))**2)

                ds = math.sqrt(2/Cf)*(MuwMuinf/math.sqrt(RhowRhoinf))*Yplus/Re

                # calculations to find approximate height of boundary layer (Very inaccurate, needs improvement)
                H = 0.16*Length*((FSV*Rhoinf*Length/Mu)**-0.1428)*3.28*12
                Counter = 0
                Delta = 0

                while Delta < H:
                    Delta = Delta + ds * (Sv ** Counter)
                    Counter = Counter + 1

                H = Delta
                Ncell = Counter

                # Calculation for wake spread angle
                Additive = 5*math.pi/180
                BodyX = numpy.array([[1], [0], [0]])
                Spread_Angle = math.acos(numpy.vdot(BodyX, WindXComponents))+Additive

                if Spread_Angle >= 59.9*math.pi/180:
                    Spread_Angle = 59.9*math.pi/180
                # Makes sure spread angle doesn't go above STAR's limit

                # Make string of data to write
                CommaSpace = ", "
                BeginSimName = '"D:\\\Theseus_CFD_M' + str(M) + '_A' + str(0.1 * round(10 * math.degrees(Alpha))) + 'B_' + str(0.1 * round(10 * math.degrees(Beta))) + '.sim");'
                MadeData = "templateActivity(" + str(M) + CommaSpace + str(FSV) + CommaSpace + str(VelocityComponents.item(0)) + CommaSpace + str(VelocityComponents.item(1)) + CommaSpace + str(VelocityComponents.item(2)) + CommaSpace + str(WindXComponents.item(0)) + CommaSpace + str(WindXComponents.item(1)) + CommaSpace + str(WindXComponents.item(2)) + CommaSpace + str(WindYComponents.item(0)) + CommaSpace + str(WindYComponents.item(1)) + CommaSpace + str(WindYComponents.item(2)) + CommaSpace + str(WindZComponents.item(0)) + CommaSpace + str(WindZComponents.item(1)) + CommaSpace + str(WindZComponents.item(2)) + CommaSpace + str(Pinf) + CommaSpace + str(Pgage) + CommaSpace + str(Rhoinf) + CommaSpace + str(Tinf) + CommaSpace + str(H) + CommaSpace + str(ds) + CommaSpace + str(Spread_Angle) + CommaSpace + BeginSimName
                # output order: Mach, Free Stream Magnitude, Free Stream Velocity Components, 3D diretional components in intertial frame, Free stream pressure, Pref, Density, Temperature, Boundary layer height, First cell height, Wake spread angle, Sim name (Eric's convention from SRT-6)

                # Print data to text file
                DataFile.write(MadeData + '\n')

                # Increase Beta
                Beta = Beta + BetaStep


            # Increase Alpha
            Alpha = Alpha + AlphaStep


        # Increase mach
        M = M + MachStep

    DataFile.close()


else:
    # all other inputs exit without running
    print('Exiting')


