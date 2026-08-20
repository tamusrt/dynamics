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
    /tools/afp/plot_live.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
     
Description:
    Plots outputs from most recent simulation

Input(s):
    filePath            Address to srt_fs_auto.dat output file
    
Output(s):
    plot_live           Figures
%}

function [ app ] = plot_live(filePath)
    close all;
    outPlots = importdata(filePath);
    [ m, n ] = size(outPlots.data);
    
    t_sim = linspace(1,m,m);
    apgMean = outPlots.data(:,1);
    apgMin = outPlots.data(:,2);
    apgMax = outPlots.data(:,3);

    out.railExitMean = outPlots.data(:,7);
    out.railExitMin = outPlots.data(:,8);
    out.railExitMax = outPlots.data(:,9);
    
    out.minStabMean = outPlots.data(:,10);
    out.minStabMin = outPlots.data(:,11);
    out.minStabMax = outPlots.data(:,12);

    out.dRangeNMean = outPlots.data(:,13);
    dRangeNMin = outPlots.data(:,14);
    dRangeNMax = outPlots.data(:,15);

    out.dRangeEMean = outPlots.data(:,16);
    dRangeEMin = outPlots.data(:,17);
    dRangeEMax = outPlots.data(:,18);
    
    dRangeMMean = outPlots.data(:,19);
    dRangeMMin = outPlots.data(:,20);
    dRangeMMax = outPlots.data(:,21);
      
%     plot(plot1,dRangeEMean,dRangeNMean)
%     grid on
%     
    plot(app.UIAxes_2,t_sim,apgMean,t_sim,apgMin,t_sim,apgMax)
    grid on
%     
%     plot(plot3,t_sim,minStabMean,t_sim,minStabMin,t_sim,minStabMax)
%     grid on
%     
%     plot(plot4,t_sim,railExitMean,t_sim,railExitMin,t_sim,railExitMax)
%     grid on
    
end 
