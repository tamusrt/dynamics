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
    /tools/afp/get_weather.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
    (L) Jimenez, Emilio             (20190212)
     
Description:
    Calls function that reads the most recent weather data log. It then
    uses the data array ouputed by that function to generate structs with 
    the data along with statistical information about said data.

Input(s):
    liveWeather     Address of the newest live data log
    
Output(s):
    weather         Struct containing newest weather data
%}

%% Reads weather data and generates atmos values
function [ weather, live_data ] = get_weather(liveWeather, afp_data)
    if isequaln(afp_data,struct([]))
        filePath = get_file_name(liveWeather); 
        file = sprintf("%s/%s",liveWeather,filePath);
        live_data = import_weather(file);
    else
        live_data = afp_data;
    end
    
    for i = 1:length(live_data.textdata)
       weather.(live_data.textdata{1,i}).data = live_data.data(:,i);
       weather.(live_data.textdata{1,i}).avg = mean(weather.(live_data.textdata{1,i}).data);
       weather.(live_data.textdata{1,i}).max = max(weather.(live_data.textdata{1,i}).data);
       weather.(live_data.textdata{1,i}).min = min(weather.(live_data.textdata{1,i}).data);
       weather.(live_data.textdata{1,i}).stdDev = std(weather.(live_data.textdata{1,i}).data);
    end
    weather.textdata = live_data.textdata;
end

