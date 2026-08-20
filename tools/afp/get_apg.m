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
    /tools/afp/get_agp.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
     
Description:
    Runs a simulation with a given oxidizer fill in lb (mass) and number 
    of Monte Carlos to obtain the apogee from said flight. It does this by 
    modifying an input file with the current weather conditions and simulation 
    settings and adding the new given oxidizer mass. 

Input(s):
    baseFile        Address of input file with current conditions
    fillOx          Mass of oxidizer in lb (mass)
    numMC           Number of Monte Carlos to be run
    timeFlight      Time duration of simulatiojs
    
Output(s):
    apg             Apogee in feet
%}

%% input file manipulation for fill
function [ apg ] = get_apg(baseFile,fillOx,numMC,timeFlight) 
    baseCell  = importdata(baseFile);
    inputCell = baseCell;
    for j = 1:length(inputCell)
        if strfind(inputCell{j},'fillOx')
            txt = sprintf('\tfillOx = %d;',fillOx);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'numMC')
            txt = sprintf('\tnumMC = %i;',numMC);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'timeFlight')
            txt = sprintf('\ttimeFlight = %d;',timeFlight);
            inputCell{j} = txt;
        end
    end
    exportFile = baseFile;
    fid = fopen(exportFile,'w');
    fprintf(fid,'%s\n',inputCell{:});
    output = load( srt_fs_main(baseFile) );
    apg = mean(max(output.out.tPos(:,:,3)));
end