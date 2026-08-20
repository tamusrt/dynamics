%{
Texas A&M University Sounding Rocketry Team
SRT-5 | 2017-2018

%-------------------% 
      TAMU SRT
 ________  ________ ________   
|\   __  \|\  _____\\   __  \  
\ \  \|\  \ \  \__/\ \  \|\  \ 
 \ \   __  \ \   __\\ \   ____\
  \ \  \ \  \ \  \_| \ \  \___|
   \ \__\ \__\ \__\   \ \__\   
    \|__|\|__|\|__|    \|__|   

%-------------------% 

Filepath:
    /tools/afp/find_nitrous.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
     
Description:
    Calculates the amount of oxidizer mass necessary to reach a target
    appogee with a given set of initial input conditions. Calls get_apg
    function to obtain the apogee of a flight with a given nitrous input 
    and then uses bisection method to obtain the right amount within a
    certain altitude tolerance.

Input(s):
    baseFile        Address of input file with current conditions
    targetApg       Target apogee in feet
    
Output(s):
    fillOx_r        Oxidizer mass in lb (mass)
%}

function [ fillOx_r ] = find_nitrous(baseFile, targetApg)
    tFlight = 45;
    numIter = 2;
    numMC = 5;
    error   = 100; 
    fill(1) =4;
    fill(2)= 28;
    fillL  = fill(1);
    fillU  = fill(2);
    apg(1)    = get_apg(baseFile,fillL,1,10); % output apgMean in srt_fs_main
    apg(2) = get_apg(baseFile,fillU,1,65);
    apgL = apg(1);
    apgU = apg(2);
    for i = 3:3+numIter
        fill(i) = ((apgU-targetApg)*fillL-(apgL-targetApg)*fillU)/(apgU-apgL);
        apg(i) = get_apg(baseFile,fill(i),numMC,tFlight);
        if abs(apg(i)-targetApg)<error % Was guess within error bound?
            fillOx_r = fill(i);
            break; % target fill found
        elseif sign(apg(i) - targetApg) == 1 % Was guess above target?
            fillU = fill(i); % Guess ---> new upper
            apgU = apg(i);
        elseif sign(apg(i) - targetApg) == -1 % Was guess below target?
            fillL = fill(i); % Guess ---> new lower
            apgL  = apg(i);
        end
    end
end
