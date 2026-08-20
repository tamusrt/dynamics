close all
clear
clc
set(0,'DefaultFigureWindowStyle','docked')

%% Initialize
cd ..;
cd ..;
outStr = "rex";
fillOx   = 8:1:16;
presTank = 350:50:950;
massDry  = 65:5:110;
railElev = 84:0.5:96;
railFrict = 0.1:0.1:1.5;
windAvg  = 0:2.5:40;
%railElev = 100:-1:80;

baseFile  = './input/hutto_10k_21.inp';
% ixxDry = (32/92);
% iyyDry = (745.84/(92));
% izzDry = (745.84/(92));
[fillOx,presTank] = meshgrid(fillOx,presTank);
baseCell      = importdata(baseFile);

for i = 1:numel(fillOx)
    
    inputCell = baseCell;
    
    for j = 1:length(inputCell)
        
        if strfind(inputCell{j},'presTank')
            
            txt = sprintf('\tpresTank = %.2f;',presTank(i));
            inputCell{j} = txt;
        elseif strfind(inputCell{j},'fillOx')
            
            txt = sprintf('\tfillOx = %.2f;',fillOx(i));
            inputCell{j} = txt;
            
%             
%         elseif strfind(inputCell{j},'tempTank')
%             
%             txt = sprintf('\ttempTank = %.2f;',tempTank(i));
%             inputCell{j} = txt;
%         elseif strfind(inputCell{j},'fillOx')
%             
%             txt = sprintf('\tfillOx = %.2f;',fillOx(i));
%             inputCell{j} = txt;
            
            
        elseif strfind(inputCell{j},'name')
            
            txt = sprintf('\tname = "the_%s_%s";',outStr,num2str(i));
            inputCell{j} = txt;
        end     
    end
   
    exportFile = sprintf('./tools/fpe/rex/input/the_rex_%i.inp',i);
    fid = fopen(exportFile,'w');
    fprintf(fid,'%s\n',inputCell{:});
    fclose(fid);
    
end
