
clc; clear; close all;
cd ..;

%%
inputFile = "./input/hutto_20181103_opt.inp"
% xGoal = -500;    % recovery position east [ft]
% yGoal = 1200;    % recovery position north [ft]
xGoal = 0;    % recovery position east [ft]
yGoal = 1000;  
apgGoal = 5000; % apogee goal [ft]
rGoal = sqrt(xGoal^2+yGoal^2);
thtGoal = atan2d(yGoal,xGoal);
goal = [rGoal; thtGoal; apgGoal];
numMClo = 1;
numMChi = 25;
iterlo  = 8;
iterhi  = 2;
tollo   = [0.005, 0.005, 0.001]; % relative tolerance
tolhi   = [0.01, 0.01, 0.005]; 
drailElev   = 0.5;
drailHead   = 0.5;
dfillOx  = 1;
dx       = [drailElev; drailHead; dfillOx];
alpha = diag([1, 1 , 1]); % iteration damping factor
earlyStop = false;
%% Algorithm 
% Need better initial guess for nitrous (run find_nitrous?)
% x(:,1) = [atand(4*apgGoal/sqrt(xGoal^2+yGoal^2)); atan2d(yGoal,xGoal); 15]; % [railElev, railHead, fillOx];
% F(:,1) = getF(inputFile, x(:,1), goal, numMClo);
% J      = getJacobian(inputFile, x, dx, F(:,1), goal, numMClo) 
% x(:,2) = J\-F(:,1) + x(:,1)
% F(:,2) = getF(inputFile,x(:,2),goal,numMClo)

x(:,1) = [atan2d(4*apgGoal,sqrt(xGoal^2+yGoal^2)); atan2d(yGoal,xGoal); 15];
F(:,1) = getF(inputFile, x(:,1), goal, numMClo,1);
J{1}   = getJacobian(inputFile, x, dx, F(:,1), goal, numMClo);
x(:,2) = -inv(J{1})*F(:,1) + x(:,1);

for i = 2:iterlo
    errorfeet = [F(1,i-1)*cosd(F(2,i-1)), F(1,i-1)*sind(F(2,i-1)), F(3,i-1)];
    if abs(errorfeet(1)) < 50 && abs(errorfeet(2)) < 50 && abs(errorfeet(3)) < 50 %Norm doesn't make sense anymore bc no longer all in feet. 
        earlyStop = true;
        x = x(:,1:end-1);
        break;
    end
    F(:,i) = getF(inputFile, x(:,i), goal, numMClo, i);
    dF = (F(:,i) - F(:,i-1));
    dx = x(:,i) - x(:,i-1);
    J{i} = J{i-1} + (dF - (J{i-1}*dx))/(norm(x(:,i))^2)*dx';
    J{i} = J{i};
%     m(:,i) = x(:,i) - inv(J{i})*F(:,i);
%     y(:,i) = getF(inputFile, m(:,i), goal, numMClo, -1) - F(:,i);
%     s(:,i) = m(:,i) - x(:,i);
%     J_m{i} = J{i} + ((y(:,i)-J{i}*s(:,i))*s(:,i)')/(norm(s(:,i))^2); %this line is fucky
%     x(:,i+1) = J{i}\-F(:,i) + x(:,i);
%     x(:,i+1) = -2*inv(J{i} - J_m{i})*F(:,i) + x(:,i);
    x(:,i+1) = (alpha*-inv(J{i})*F(:,i) + x(:,i));
end
if ~earlyStop   
    F(:,end+1) = getF(inputFile, x(:,end), goal,numMClo,size(F,2)+1);
end

% xhi(:,1) = x(:,end);
% Fhi(:,1) = getF(inputFile, xhi(:,1), goal, numMChi,1);
% Jhi{1} = J{end};
% for i = 2:iterhi
%     errorfeet = [F(1,i-1)*cosd(F(2,i-1)), F(1,i-1)*sind(F(2,i-1)), F(3,i-1)];
%     if abs(errorfeet(1)) < 50 && abs(errorfeet(2)) < 50 && abs(errorfeet(3)) < 50 %Norm doesn't make sense anymore bc no longer all in feet. 
%         earlyStop = true;
%         x = x(:,1:end-1);
%         break;
%     end
%     F(:,i) = getF(inputFile, x(:,i), goal, numMChi, i);
%     dF = (F(:,i) - F(:,i-1));
%     dx = x(:,i) - x(:,i-1);
%     J{i} = J{i-1} + (dF - (J{i-1}*dx))/(norm(x(:,i))^2)*dx';
%     J{i} = J{i};
%     x(:,i+1) = alpha*(-inv(J{i})*F(:,i) + x(:,i));
% end

for i = 1:size(F,2)
    normF(i) = norm(F(:,i));
    xcoord   = F(1,i)*cosd(F(2,i));
    ycoord   = F(1,i)*sind(F(2,i));
    coord(:,i) = [xcoord; ycoord];
end
figure()
plot(normF)
figure()
plot(coord(1,:), coord(2,:),'Marker','*');




function [J] = getJacobian(inputPath, x, dx, val, goal, numMC)
   
    dFdrailElev = (getF(inputPath, x + [dx(1); 0; 0], goal, numMC,-1) - val)/dx(1);
    dFdrailHead = (getF(inputPath, x + [0; dx(2); 0], goal, numMC,-1) - val)/dx(2);
    dFdfillOx   = (getF(inputPath, x + [0; 0; dx(3)], goal, numMC,-1) - val)/dx(3);
    J = [dFdrailElev, dFdrailHead, dFdfillOx];

end



function [F] = getF(inputPath, x, goal, numMC,n)
    if x(3) > 26
        x(3) = 26;
    end
    if x(3) < 6
        x(3) = 6;
    end
    fid = fopen(inputPath);
    baseCell      = textscan(fid,'%s','Delimiter','');
    inputCell     = baseCell{1};
    for j = 1:length(inputCell)
        if strfind(inputCell{j},'fillOx')
            txt = sprintf('\tfillOx = %d;',x(3));
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'numMC')
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
        elseif strfind(inputCell{j}, 'name')
            txt = sprintf('\tname = "recov_opt%s";',num2str(n));
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
    apg = mean(max(out.tPos(:,:,3)))
    val = [rAvg; thtAvg; apg];
    
    F = val-goal;
end

