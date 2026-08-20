clear all;close all;clc;fclose('all');

% ip = "192.168.1.5";
% port = 4040;
timestep = 16; %seconds

%Conversions
ms2mph = 2.23694; %meters per second to mph. The WS sends data in meters per second

%Exit Button
ButtonHandle = uicontrol('Style', 'PushButton', ...
                         'String', 'Stop loop', ...
                         'Callback', 'delete(gcbf)');
                     
Time_Vec = datetime.empty;
TemperatureC_Vec = double.empty;
Humitity_Vec = double.empty;
WindDir_Vec = double.empty;
WindAvg_Vec = double.empty;
WindGust_Vec = double.empty;

%add zeros to the first rows of data
initcount = 10;
TemperatureC_Vec = zeros(initcount,1);
Humitity_Vec = zeros(initcount,1);
WindDir_Vec = zeros(initcount,1);
dir_u_vec = zeros(initcount,1);
dir_v_vec = zeros(initcount,1);
WindAvg_Vec = zeros(initcount,1);
WindGust_Vec = zeros(initcount,1);

figure;
subplot(2,3,1);
xlabel('Time');
ylabel('Temperature [Celcius]');
title('Temperature over Time');

subplot(2,3,4);
xlabel('Time');
ylabel('Humidity [%]');
title('Humidity over Time');

subplot(2,3,2);
xlabel('Time');
ylabel('Wind Average Speed [MPH]');
title('Wind Average Speed over Time');

subplot(2,3,5);
xlabel('Time');
ylabel('Wind Gust Speed [MPH]');
title('Wind Gust Speed over Time');

subplot(2,3,3);
title('Wind Direction over Time');
view(90,-90);


% t=tcpip(ip, port, 'NetworkRole', 'client');
% fopen(t)
fid=fopen('log.txt'); 
samples = 1;
while true
%     datainput = fscanf(t,'%s')
            thisline = fgetl(fid);
            if ~ischar(thisline)
                break;
            end
            datainput = thisline;

    [Data_packet_Struc,useless] = parse_json(datainput);
    Data_packet_cells = struct2cell(Data_packet_Struc{1,1});

    DateTime = datetime(cell2mat(Data_packet_cells(1)),'InputFormat','yyyy-MM-ddHH:mm:ss');
    Time_Vec = [Time_Vec;DateTime];
    
    if samples == 1
        T_init = Time_Vec(1);
        %add time labels to intialization data
        timestepsbackwards = T_init-transpose(fliplr(seconds(timestep:timestep:timestep*initcount)));
        Time_Vec = [timestepsbackwards;T_init];
    end
    TemperatureC_Vec = [TemperatureC_Vec;cell2mat(Data_packet_cells(4))];
    Humitity_Vec = [Humitity_Vec;cell2mat(Data_packet_cells(5))];
    WindDir_Vec = [WindDir_Vec;cell2mat(Data_packet_cells(6))];
    dir_u_vec(samples+initcount) = cosd(WindDir_Vec(samples+initcount));
    dir_v_vec(samples+initcount) = sind(WindDir_Vec(samples+initcount));
    WindAvg_Vec = [WindAvg_Vec;ms2mph*cell2mat(Data_packet_cells(7))];
    WindGust_Vec = [WindGust_Vec;ms2mph*cell2mat(Data_packet_cells(8))];

    %scale down old u/v vectors to show time passage
    dir_u_vec(samples+initcount-10:samples+initcount-1) = 0.75.*dir_u_vec(samples+initcount-10:samples+initcount-1);
    dir_v_vec(samples+initcount-10:samples+initcount-1) = 0.75.*dir_v_vec(samples+initcount-10:samples+initcount-1);
    
    %plot data
    subplot(2,3,1);
    plot(Time_Vec(end-10:end),TemperatureC_Vec(end-10:end),'Linewidth',3);
        xlabel('Time');
        ylabel('Temperature [Celcius]');
        title('Temperature over Time');

    subplot(2,3,4);
    plot(Time_Vec(end-10:end),Humitity_Vec(end-10:end),'Linewidth',3);
        xlabel('Time');
    ylabel('Humidity [%]');
    title('Humidity over Time');
    
    subplot(2,3,2);
    plot(Time_Vec(end-10:end),WindAvg_Vec(end-10:end),'Linewidth',3);
        xlabel('Time');
    ylabel('Wind Average Speed [MPH]');
    title('Wind Average Speed over Time');
    
    subplot(2,3,5);
    plot(Time_Vec(end-10:end),WindGust_Vec(end-10:end),'Linewidth',3);
        xlabel('Time');
        ylabel('Wind Gust Speed [MPH]');
        title('Wind Gust Speed over Time');
    
    subplot(2,3,3);
    compass(dir_u_vec(end-10:end),dir_v_vec(end-10:end));
    title('Wind Direction over Time');
    view(90,-90);
    
      pause(1);
      %pause(16);
      
      %exit case, close the TCP connection
      if ~ishandle(ButtonHandle)
    disp('Loop stopped by user');
    % fclose(t);
    break;
      end

 samples = samples+1;
end


% fclose(t);
fclose(fid);