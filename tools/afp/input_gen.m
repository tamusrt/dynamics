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
    /tools/afp/input_gen.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
     
Description:
    Updates input file with current weather and oxidizer conditions along
    with a new numer of Monte Carlo simulations to be executed in the
    upcoming simulation.

Input(s):
    baseFile            Address to original input file
    weather             Struct containing updated weather data
    simMC               Number of Monte Carlos to be performed
    
Output(s):
    srt_fs_auto.inp     Input file
%}

%% Generates new input file with updated weather data
function [ ] = input_gen(baseFile, weather, simMC, railElev, railHead, Ox_Override, fillox)  
    % Opens skeleton input and merges skeleton input and weather data
    inputCell  = importdata(baseFile);
    
    for i = 1:length(weather.textdata)
        for j = 1:length(inputCell)
            % Looks for variable in file
            if strfind(inputCell{j}, weather.textdata{i})
                % Variable Found
                if strfind(inputCell{j+1}, "{")
                    %   If the following cell contains a bracket, it is a
                    %   distribution
                    if strfind(inputCell{j+2}, "Uniform") 
                        lower = weather.(weather.textdata{i}).min;
                        upper = weather.(weather.textdata{i}).max;
                        
                        inputCell{j+3} = lower;
                        inputCell{j+4} = upper;                        
                    elseif strfind(inputCell{j+2}, "Normal")
                        mean   = sprintf('\t\tmean  = %d;',weather.(weather.textdata{i}).avg);
                        stdDev = sprintf('\t\tstdDev  = %d;',weather.(weather.textdata{i}).stdDev);
                        low    = sprintf('\t\tlower  = %d;',weather.(weather.textdata{i}).min);
                        upper  = sprintf('\t\tupper  = %d;',weather.(weather.textdata{i}).max);
                        
                        inputCell{j+3} = mean;
                        inputCell{j+4} = stdDev;  
                        inputCell{j+5} = low;
                        inputCell{j+6} = upper;
                    elseif strfind(inputCell{j+2}, "Half Normal")
                        mean   = sprintf('\t\tmean  = %d;',weather.(weather.textdata{i}).avg);
                        stdDev = sprintf('\t\tstdDev  = %d;',weather.(weather.textdata{i}).stdDev);
                        low    = sprintf('\t\tlower  = %d;',weather.(weather.textdata{i}).min);
                        upper  = sprintf('\t\tupper  = %d;',weather.(weather.textdata{i}).max);
                        
                        inputCell{j+3} = mean;
                        inputCell{j+4} = stdDev;  
                        inputCell{j+5} = low;
                        inputCell{j+6} = upper; 
                    elseif strfind(inputCell{j+2}, "Weiner")
                        mean   = sprintf('\t\tmean  = %d;',weather.(weather.textdata{i}).avg);
                        stdDev = sprintf('\t\tstdDev  = %d;',weather.(weather.textdata{i}).stdDev);
                                                
                        inputCell{j+3} = mean;
                        inputCell{j+4} = stdDev;
                    elseif strfind(inputCell{j+2}, "Half Weiner")
                        mean   = sprintf('\t\tmean  = %d;',weather.(weather.textdata{i}).avg);
                        stdDev = sprintf('\t\tstdDev  = %d;',weather.(weather.textdata{i}).stdDev);
                                                
                        inputCell{j+3} = mean;
                        inputCell{j+4} = stdDev;             
                    elseif strfind(inputCell{j+2}, "LHS")
                        low    = sprintf('\t\tlower  = %d;',weather.(weather.textdata{i}).min);
                        upper  = sprintf('\t\tupper  = %d;',weather.(weather.textdata{i}).max);
                        
                        inputCell{j+3} = low;
                        inputCell{j+4} = upper;  
                    else
                        warning("Unknown distribution %s used in input file", inputCell{j+2});
                    end
                else
                    %   If the following is not a bracket, it is an average
                    mean   = sprintf('\t%s\t=\t%d;',weather.textdata{i},weather.(weather.textdata{i}).avg);
                    inputCell{j} = mean;
                end
            end
        end
    end

    for j = 1:length(inputCell)
        if strfind(inputCell{j}, "numMC")
           simMC = sprintf("\tnumMC = %d;",simMC);
           inputCell{j} = simMC;
        end
    end
    
    %% Edits file for rail elevation if input (only normal dist, std = 0.5)
    if railElev ~= 0
        for j = 1:length(inputCell)
            % Looks for variable in file
            if strfind(inputCell{j}, "railElev")
                % Variable Found
                if strfind(inputCell{j+1}, "{")
                    %   If the following cell contains a bracket, it is a
                    %   distribution
                    if strfind(inputCell{j+2}, "Normal")
                        mean   = sprintf('\t\tmean  = %d;', railElev);
                        stdDev = 0.5;
                        stdDev = sprintf('\t\tstdDev  = %d;', stdDev);
                        
                        inputCell{j+3} = mean;
                        inputCell{j+4} = stdDev;  
                    else
                        warning("Unknown distribution %s used in input file", inputCell{j+2});
                    end
                else
                    %   If the following is not a bracket, it is an average
                    mean   = sprintf('\t%s\t=\t%d;',mean, railElev);
                    inputCell{j} = mean;
                end
            end
        end
    end
    
    %% Edits file for rail heading if input (only normal dist, std = 1)
    if railHead ~= 0
        for j = 1:length(inputCell)
            % Looks for variable in file
            if strfind(inputCell{j}, "railHead")
                % Variable Found
                if strfind(inputCell{j+1}, "{")
                    %   If the following cell contains a bracket, it is a
                    %   distribution
                    if strfind(inputCell{j+2}, "Normal")
                        mean   = sprintf('\t\tmean  = %d;', railHead);
                        stdDev = 1;
                        stdDev = sprintf('\t\tstdDev  = %d;', stdDev);
                        
                        inputCell{j+3} = mean;
                        inputCell{j+4} = stdDev;  
                    else
                        warning("Unknown distribution %s used in input file", inputCell{j+2});
                    end
                else
                    %   If the following is not a bracket, it is an average
                    mean   = sprintf('\t%s\t=\t%d;',mean, railElev);
                    inputCell{j} = mean;
                end
            end
        end
    end
    
    %% Edits file for oxfill if input
    if Ox_Override ~= 0
        for j = 1:length(inputCell)
            % Looks for variable in file
            if strfind(inputCell{j}, "fillOx")
                % Variable Found
                if strfind(inputCell{j+1}, "{")
                    %   If the following cell contains a bracket, it is a
                    %   distribution
                    if strfind(inputCell{j+2}, "Normal")
                        mean   = sprintf('\t\tmean  = %d;', railHead);
                        stdDev = 1;
                        stdDev = sprintf('\t\tstdDev  = %d;', stdDev);
                        
                        inputCell{j+3} = mean;
                        inputCell{j+4} = stdDev;  
                    else
                        warning("Unknown distribution %s used in input file", inputCell{j+2});
                    end
                else
                    %   If the following is not a bracket, it is an average
                    mean   = sprintf('\t%s\t=\t%d;','fillOx', fillox);
                    inputCell{j} = mean;
                end
            end
        end
    end
   
        
    % Generates input file
    exportFile = baseFile;
    fid = fopen(exportFile,'w');
    fprintf(fid,'%s\n',inputCell{:});
end
