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
    /tools/afp/import_weather.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
    (L) Jimenez, Emilio             (20190224)
     
Description:
    Reads newest weather data log and records data as a struct containing
    a data array along with a cell array containing the headers
    corresponding to the data. Due to the format of the logs, they don't
    contain any headers and but rather use a character to indicate what 
    parameter the following number is refering to. The program reads the
    first line of the file and obtains the format being used by the log and
    creates the appropriate headers. It then procedes to read the rest of 
    the file using the format detected in the first line.

Input(s):
    file        Address of the newest live data log
    
Output(s):
    live_data   Struct containing newest weather data and headers
%}

function live_data = import_weather(file)
    % Opens file
    fileID = fopen(file);
    
    % Reads first line and obtains data format and headers
    line = fgetl(fileID);
    if contains(line, '<your headers here>')
        line = fgetl(fileID);
    end
    
    if line((2-1):2) == 'st'
        k = 1;
        headers = strings;
        for i = 2:length(line)
           if line((i-1):i) == 'st'
               headers(k) = 'st';
               live_data.textdata{k} = 'timeStamp';
               k = k + 1;
           elseif line((i-1):i) == 'al'
               headers(k) = 'al';
               live_data.textdata{k} = 'elev';
               k = k + 1;
           elseif line((i-1):i) == 'tm'
               headers(k) = 'tm';
               live_data.textdata{k} = 'tempAmb';
               k = k + 1;
           elseif line((i-1):i) == 'br'
               headers(k) = 'br';
               live_data.textdata{k} = 'ambPress';
               k = k + 1;
           elseif line((i-1):i) == 'hm'
               headers(k) = 'hm';
               live_data.textdata{k} = 'relHum';
               k = k + 1;
           elseif line((i-1):i) == 'an'
               headers(k) = 'an';
               live_data.textdata{k} = 'windAvg';
               k = k + 1;
           elseif line((i-1):i) == 'sw'
               headers(k) = 'sw';
               live_data.textdata{k} = 'fillWeight';
               k = k + 1;
           elseif line((i-1):i) == 'sp'
               headers(k) = 'sp';
               live_data.textdata{k} = 'fillPress';
               k = k + 1;
           elseif line((i-1):i) == 'pw'
               headers(k) = 'pw';
               live_data.textdata{k} = 'tankWeight';
               k = k + 1;
           end
        end

        line = sprintf('%s%%f%%c%%f%%c%%f',headers(1));
        line_format = line;
        for k = 2:length(headers)
            line = sprintf('%s%%f',headers(k));
            line_format = sprintf('%s,%s', line_format, line);
        end

        % Reads, extracts, and assambles data into an array
        i = 1;
        while fgetl(fileID) ~= -1
            C = textscan(fileID,line_format);
            [n, m] = size(C);
            %Doesn't start from 1 since we don't utilize time step
            for j = 6:m
                val = C{1,j};
                if isempty(val)
                    break
                else
                    if j == 10
                        % Convert mph to ft/s from input file
                        live_data.data(1:size(val,1),j-5) = val.*22/15;
                    else
                        live_data.data(1:size(val,1),j-5) = val;
                    end
                end
            end
            i = i + 1;
        end 

        live_data.textdata = live_data.textdata(2:9);

        % Closes file
        fclose(fileID);
    else
        k = 1;
        headers = strings;
        for i = 1:length(line)
           if line(i) == 'a'
               headers(k) = 'a';
               live_data.textdata{k} = 'elev';
               k = k + 1;
           elseif line(i) == 't'
               headers(k) = 't';
               live_data.textdata{k} = 'tempAmb';
               k = k + 1;
           elseif line(i) == 'b'
               headers(k) = 'b';
               live_data.textdata{k} = 'ambPress';
               k = k + 1;
           elseif line(i) == 'h'
               headers(k) = 'h';
               live_data.textdata{k} = 'relHum';
               k = k + 1;
           elseif line(i) == 'n'
               headers(k) = 'n';
               live_data.textdata{k} = 'windAvg';
               k = k + 1;
           elseif line(i) == 'f'
               headers(k) = 'f';
               live_data.textdata{k} = 'fillWeight';
               k = k + 1;
           elseif line(i) == 'p'
               headers(k) = 'p';
               live_data.textdata{k} = 'fillPress';
               k = k + 1;
           elseif line(i) == 'w'
               headers(k) = 'w';
               live_data.textdata{k} = 'tankWeight';
               k = k + 1;
           end
        end

        line = sprintf('%s%%f',headers(1));
        line_format = line;
        for k = 2:length(headers)
            line = sprintf('%s%%f',headers(k));
            line_format = sprintf('%s,%s', line_format, line);
        end

        % Reads, extracts, and assambles data into an array
        i = 1;
        while fgetl(fileID) ~= -1
            C = textscan(fileID,line_format);
            [n, m] = size(C);
            for j = 1:m
                val = C{1,j};
                if isempty(val)
                    break
                else
                    live_data.data(i,j) = val;
                end
            end
            i = i + 1;
        end 

        % Closes file
        fclose(fileID);
    end
end