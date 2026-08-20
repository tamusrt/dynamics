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
    /tools/afp/get_file_name.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
     
Description:
    Accesses the folder with weather data logs and obtains the name of the 
    most recent complete file. It does this by collecting the dates of every 
    file in the folder and sorting them to obtain the file name of the newest 
    one.

Input(s):
    liveWeather     Address of folder with live data logs
    
Output(s):
    fileName        Name of the most recent data log
%}

function [ fileName ] = get_file_name(liveWeather)
ftpobj = liveWeather;
server = dir(ftpobj);

dates = [server(:).datenum];
[~,i] = sort(dates);
imin = max(i-1);
fileName = server(imin).name;
end 