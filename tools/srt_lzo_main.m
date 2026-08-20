
function [outputPath] = srt_lzo_main(varargin)
clear
close all
clc
 
%% Target landing, apogee
goal.x          = 0;    % recovery position east [ft]
goal.y          = 3000;  
goal.apg        = 10000; % apogee goal [ft]  

%% Simulation settigs
sim.numMCFill   = 10;
sim.numMClo     = 1;
sim.numMChi     = 5;
sim.iterlo      = 2;
sim.iterhi      = 3;

%% Load User Input

addpath("..");

if nargin == 0
    [fileName,fileDir,~] = uigetfile('./lzo_input/*.inp*','Select Input File');
    sim.filePath = [fileDir fileName];
else
    sim.filePath = varargin{1};
end

% define deltas for initial jacobian evaluation
drailElev   = 0.1;
drailHead   = 1;
dfillOx     = 1;
dx          = [drailElev; drailHead];

rGoal = sqrt(goal.x^2+goal.y^2);
thtGoal = atan2d(goal.y,goal.x);
goal.r = rGoal;
goal.tht = thtGoal

% fill(1) = srt_lzo_nitrous(goal,sim)
fill(1) = 15;
x(:,1) = [ atan2d(4*goal.apg,sqrt(goal.x^2+goal.y^2)); atan2d(goal.y,goal.x)]; % [railElev, railHead] --> Initial guess
[data{1}, F(:,1)] = getF(sim.filePath, x(:,1), goal, sim.numMClo);
% J{1}   = getJacobian(sim.filePath, x, dx, F(:,1), goal, sim.numMClo);
J{1} = eye(2).*1000
% x(:,2) = -inv(J{1})*F(:,1) + x(:,1); 

for i = 1:sim.iterlo
    x(:,i+1) = -inv(J{i})*F(:,i) + x(:,i); 
%     if abs(errorfeet(1)) < 50 && abs(errorfeet(2)) < 50 && abs(errorfeet(3)) < 50 %Norm doesn't make sense anymore bc no longer all in feet. 
%         earlyStop = true;
%         x = x(:,1:end-1);
%         break;
%     end
    [data{i}, F(:,i+1)] = getF(sim.filePath, x(:,i+1), goal, sim.numMClo);
    dF = (F(:,i+1) - F(:,i));
    dx = x(:,i+1) - x(:,i);
    J{i+1} = J{i} + (dF - (J{i}*dx))/(norm(x(:,i+1))^2)*dx';
    %J{i} = J{i};
%     x(:,i+1) = (alpha*-inv(J{i})*F(:,i) + x(:,i));
end

a = 0;

function [data, F] = getF(inputPath, x, goal, numMC)
    
    fid = fopen(inputPath);
    baseCell      = textscan(fid,'%s','Delimiter','');
    inputCell     = baseCell{1};
    for j = 1:length(inputCell)
        if strfind(inputCell{j}, 'numMC')
            txt = sprintf('\tnumMC = %i;',numMC);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'railHead')
            txt = sprintf('\trailHead = %d;',x(2));
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'railElev')
            txt = sprintf('\trailElev = %d;',x(1));
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'runRecov')
            txt = sprintf('\trunRecov = %s;',"true");
            inputCell{j} = txt;
        end
    end
    exportFile = inputPath;
    fid = fopen(exportFile,'w');
    fprintf(fid,'%s\n',inputCell{:});
    outputPath = srt_fs_main(inputPath);
    data = load(outputPath);
    out = data.out;
    xImp = zeros(1,numMC);
    yImp = zeros(1,numMC);
    for i = 1:numMC
        impind = find(out.iPos(:,i,3) <= 0,1)-1;
        xImp(i) = out.iPos(impind,i,1);
        yImp(i) = out.iPos(impind,i,2);
    end
    xAvg = mean(xImp)
    yAvg = mean(yImp)
    rAvg = sqrt(xAvg^2 + yAvg^2)
    thtAvg = atan2d(yAvg,xAvg)
    val = [rAvg; thtAvg];
    
    F = val-[goal.r; goal.tht];
end

function [J] = getJacobian(inputPath, x, dx, val, goal, numMC)
    
    % Evaluate function
    [~, FrailElev] = getF(inputPath, x + [dx(1); 0], goal, numMC)
    [~, FrailHead] = getF(inputPath, x + [0; dx(2)], goal, numMC)
    
    % Numeric derivative using previous point
    dFdrailElev = (FrailElev - val)/dx(1);
    dFdrailHead = (FrailElev - val)/dx(2);
    
    
    J = [dFdrailElev, dFdrailHead];

end

end
