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
    /srt_fs_afp.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
     
Description:
    Automatically runs simulations using live weather data and
    current oxydizer conditions.
    
Input(s):
    srt_fs_auto.inp     Input file
    
Output(s):
    fs_auto_out.dat     Data file
%}

function [] = srt_fs_afp(varargin)
    close all; clc;
    addpath('./tools/afp/')
    
    if nargin == 0
        [fileName,fileDir,~] = uigetfile('./input/*.inp*','Select Input File');
                    filePath = [fileDir fileName];
    else
        filePath = varargin{1};
    end  
    
    %% Obtains automation parameters from input file
    [sim,flt,del,atm,bod,prp] = srt_fs_load(filePath);
    targetApg = sim.targetApg;
    liveSimMC = sim.liveSimMC; 
    liveWeather = atm.liveWeather;
    
    %% Time delay
    % Time in minute intervals
    t = [10 10 10 10 10 5]*60; 
    if sim.troubleShoot == 1
        t = [1 1]*60;
        liveWeather = 'C:/Users/aguil/Documents/GitHub/playground/afp/input/weather';
    end
    tsimStart = tic;
    
    % Run single sim
    fileID = fopen('./output/fs_auto_out.dat','w');
    log_path = fopen('./output/afp_log.dat','w');
    fprintf(fileID,'apgMean\tapgMin\tapgMax\tfillOxMean\tfillOxMin\tfillOxMax\trailExitMean\trailExitMin\trailExitMax\tminStabMean\tminStabMin\tminStabMax\trunTimeMean\trunTimeMin\trunTimeMax\tdRangeNMean\tdRangeNMin\tdRangeNMax\tdRangeEMean\tdRangeEMin\tdRangeEMax\tdRangeMMean\tdRangeMMin\tdRangeMMax\n');
    run_sim(filePath, fileID, log_path, liveWeather, targetApg, liveSimMC, sim);
    fclose(fileID);
    fclose(log_path);
    tsim = toc(tsimStart);
    
    % Print Plot
%     plot_live('./output/fs_auto_out.dat');
    
    for j = 1:length(t)
        % Wait until next sim
        time_delay(t,j,tsim);
        
        % Run single sim
        tsimStart = tic;
        fileID = fopen('./output/fs_auto_out.dat','a');
        log_path = fopen('./output/afp_log.dat','a');
        run_sim(filePath, fileID, log_path,liveWeather, targetApg, liveSimMC, sim);
        fclose(fileID);
        fclose(log_path);
        
        % Print Plot 
        plot_live('./output/fs_auto_out.dat');
        
        tsim = toc(tsimStart); 
        
        %% Continues Sims Indefinitely 
        if j == length(t)
            while true
                % Wait until next sim
                time_delay(t,j,tsim);
                
                % Run single sim
                tsimStart = tic;
                fileID = fopen('./output/fs_auto_out.dat','a');
                log_path = fopen('./output/afp_log.dat','a');
                run_sim(filePath, fileID, log_path,liveWeather, targetApg, liveSimMC, sim);
                fclose(fileID);
                fclose(log_path);
                                
                % Print Plot 
                plot_live('./output/fs_auto_out.dat');
                tsim = toc(tsimStart);
            end
        end
    end     
end