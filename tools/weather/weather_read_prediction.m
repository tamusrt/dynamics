
clc; clear; close all;
set(0,'DefaultFigureWindowStyle','docked')
mph2ftps = 1.4667;
file = "./irec18/irec18.csv";
data = readtable(file);
meanTemp = table2array(data(:,'HLY_TEMP_NORMAL'));
tenTemp = table2array(data(:,'HLY_TEMP_10PCTL'));
nineTemp = table2array(data(:,'HLY_TEMP_90PCTL'));

meanDew = table2array(data(:,'HLY_DEWP_NORMAL'));
tenDew = table2array(data(:,'HLY_DEWP_10PCTL'));
nineDew = table2array((data(:,'HLY_DEWP_90PCTL')));

wind = table2array(data(:,'HLY_WIND_AVGSPD'));
windDir = table2array(data(:,'HLY_WIND_VCTDIR'));

time = zeros(numel(data(:,'DATE')),1);
for i = 1:numel(time)
    time(i) = 6+i-1;
end
far2cel = @(T) (T - 32) .* (5/9);
humFun = @(T,TD) 100.*(exp((17.625.*TD)./(243.04+TD))./exp((17.625.*T)./(243.04+T)));
meanHum = humFun(far2cel(meanTemp),far2cel(meanDew));
tenHum = humFun(far2cel(tenTemp),far2cel(tenDew));
nineHum = humFun(far2cel(nineTemp),far2cel(nineDew));
hum = [tenHum meanHum nineHum];
target = 12; % Target index of 12PM
temp = [tenTemp meanTemp nineTemp];
wind = wind * mph2ftps; % mph to ft/s
windDir = 90 - windDir + 360; %converts direction to fs frame

% Additional Calculations

minTemp = min(temp(:,1))
meanTemp = mean(temp(:,2))
maxTemp = max(temp(:,3))

minTempW = min(temp(:,1))
meanTempW = mean(temp(:,2))
maxTempW = max(temp(:,3))

minHum = min(tenHum)
meanHum = mean(meanHum)
maxHum = max(nineHum)

minWind = min(wind)
meanWind = mean(wind)
maxWind = max(wind)
stdWind = std(wind)

minWindDir = min(windDir)
meanWindDir = mean(windDir)
maxWindDir = max(windDir)
stdWindDir = std(windDir)

file = "./irec18/irec18_prediction.csv";
data = readtable(file);
predTemp = table2array(data(:,'temp'));
predHum  = table2array(data(:,'hum'));
predWind = table2array(data(:,'wind'))*mph2ftps;
predWinddir = table2array(data(:,'windDir'));
predPress = table2array(data(:,'press'));


%% plotting
lineWidth = 2;
launchGoal = [target-1 , target, target+1];
date = 'June 21st';
%Humidity
hold on 
l1 = plot(time,hum(:,1),'Color','b','LineWidth', lineWidth);
l2 = plot(time,hum(:,2),'Color','k','LineWidth', lineWidth);
l3 = plot(time,hum(:,3),'Color','r','LineWidth', lineWidth);
l4 = plot(time,predHum,'Color','g','LineWidth', lineWidth);
%place holder plot for grey color
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
q = patch(qp,qp,grey);
q.EdgeColor = grey;
ylabel("Humidity [ % ]");
xlabel("Time");
ylim([0,100]);
ax = gca;
xticks(time);
xTxt = {"6:00", "7:00" , "8:00" "9:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"...
    "16:00", "17:00", "18:00"};
ax.XTickLabel = xTxt;
grid on
title(['Humidity | ' date '| 30 Year Normal']);
xP2  = [launchGoal flip(launchGoal)];
ind1 = launchGoal(1) -time(1)+1;
ind2 = launchGoal(2) -time(1)+1;
ind3 = launchGoal(3) -time(1)+1;
yP2 = [ hum(ind1,1),hum(ind2,1),hum(ind3,1),hum(ind3,3),hum(ind2,3),hum(ind1,3)];
% p2 = patch(xP2,yP2,1);
% p2.FaceColor   = 'k';
% p2.FaceAlpha   = 0.5 / 3;


titleFont = 30;
labelFont = 22;
axisWidth = 1.5;
axisColor = 'k';
gridAlpha = 0.5;
gridStyle = '-';

ax                = gca;
ax.XTickLabel = xTxt;
ax.FontName       = 'Franklin Gothic Book';
ax.XColor         = axisColor;
ax.YColor         = axisColor;
ax.FontSize       = labelFont;
ax.LineWidth      = axisWidth;
ax.Title.FontSize = titleFont;
% lgd= legend([l3, l2, l1, l4, q],"90th Percentile", "Mean", "10th Percentile", "Predicted", "Launch Window");
lgd= legend([l3, l2, l1, l4],"90th Percentile", "Mean", "10th Percentile", "Predicted");
lgd.FontSize = labelFont;

ax.XGrid         = 'on';
ax.YGrid         = 'on';
ax.GridLineStyle = gridStyle;
ax.GridColor     = axisColor;
ax.YAxis.Exponent = 0;

%Temperature
figure()
hold on
l1=plot(time,temp(:,1),'Color','b','LineWidth', lineWidth);
l2=plot(time,temp(:,2),'Color','k','LineWidth', lineWidth);
l3=plot(time,temp(:,3),'Color','r','LineWidth', lineWidth);
l4 = plot(time,predTemp,'Color','g','LineWidth', lineWidth);
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
q = patch(qp,qp,grey);
q.EdgeColor = grey;

ylabel("Temperature [ ^{o}F ]");
xlabel("Time");
ylim([min(temp(:,1))-10, max(temp(:,3))+10]);
grid on;
title(['Temperature | ' date '| 30 Year Normal']);
ind1 = launchGoal(1) -time(1)+1;
ind2 = launchGoal(2) -time(1)+1;
ind3 = launchGoal(3) -time(1)+1;
yP2 = [ temp(ind1,1),temp(ind2,1),temp(ind3,1),temp(ind3,3),temp(ind2,3), temp(ind1,3)];
% p2 = patch(xP2,yP2,1);
% p2.FaceColor   = 'k';
% 
% p2.FaceAlpha   = 0.5 / 3;

titleFont = 30;
labelFont = 22;
axisWidth = 1.5;
axisColor = 'k';
gridAlpha = 0.5;
gridStyle = '-';

ax                = gca;
xticks(time);
ax.XTickLabel = xTxt;
ax.FontName       = 'Franklin Gothic Book';
ax.XColor         = axisColor;
ax.YColor         = axisColor;
ax.FontSize       = labelFont;
ax.LineWidth      = axisWidth;
ax.Title.FontSize = titleFont;

ax.XGrid         = 'on';
ax.YGrid         = 'on';
ax.GridLineStyle = gridStyle;
ax.GridColor     = axisColor;
ax.YAxis.Exponent = 0;
% lgd = legend([l3, l2, l1, l4, q],"90th Percentile", "Mean", "10th Percentile", "Predicted", "Launch Window");
lgd = legend([l3, l2, l1, l4],"90th Percentile", "Mean", "10th Percentile");
lgd.FontSize = labelFont;
lgd.Location = 'best';
box on;

%Wind Direction
figure()
hold on;
l1=plot(time,windDir,'Color','k','LineWidth',lineWidth);
l1=plot(time,predWinddir,'Color','g','LineWidth',lineWidth);
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
% q = patch(qp,qp,grey);
% q.EdgeColor = grey;

ylabel("Wind Dir [ deg CCW from East ]");
% ylim([min(windDir) - 5, max(windDir) + 5]);
ylim([0, 360]);
xlabel("Time");
grid on;
title(['Wind Direction | ' date '| 30 Year Normal']);
% yP2 = [min(windDir) - 5, max(windDir) + 5, max(windDir) + 5, min(windDir) - 5];
yP2 = [0 360 360 0];
xP2 = [launchGoal(1), launchGoal(1), launchGoal(3) ,launchGoal(3)];
% p2 = patch(xP2,yP2,1);
% p2.FaceColor   = 'k';

p2.FaceAlpha   = 0.5 / 3;


titleFont = 30;
labelFont = 22;
axisWidth = 1.5;
axisColor = 'k';
gridAlpha = 0.5;
gridStyle = '-';

ax                = gca;
xticks(time);
ax.XTickLabel = xTxt;
ax.FontName       = 'Franklin Gothic Book';
ax.XColor         = axisColor;
ax.YColor         = axisColor;
ax.FontSize       = labelFont;
ax.LineWidth      = axisWidth;
ax.Title.FontSize = titleFont;

ax.XGrid         = 'on';
ax.YGrid         = 'on';
ax.GridLineStyle = gridStyle;
ax.GridColor     = axisColor;
ax.YAxis.Exponent = 0;
lgd = legend([l1,l2],"Mean","Predicted");
lgd.FontSize = labelFont;

figure()
hold on;
l1=plot(time,wind,'Color','k','LineWidth',lineWidth);
l2=plot(time,predWind,'Color','g','LineWidth',lineWidth);
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
% q = patch(qp,qp,grey);
% q.EdgeColor = grey;

ylabel("Wind Speed [ ft/s ]");
ylim([0, 20]);
xlabel("Time");
grid on;
title(['Wind Velocity | ' date '| 30 Year Normal']);
yP2 = [0, 20, 20, 0];
xP2 = [launchGoal(1), launchGoal(1), launchGoal(3) ,launchGoal(3)];
% p2 = patch(xP2,yP2,1);
% p2.FaceColor   = 'k';
% 
% p2.FaceAlpha   = 0.5 / 3;


titleFont = 30;
labelFont = 22;
axisWidth = 1.5;
axisColor = 'k';
gridAlpha = 0.5;
gridStyle = '-';

ax                = gca;
xticks(time);
ax.XTickLabel = xTxt;
ax.FontName       = 'Franklin Gothic Book';
ax.XColor         = axisColor;
ax.YColor         = axisColor;
ax.FontSize       = labelFont;
ax.LineWidth      = axisWidth;
ax.Title.FontSize = titleFont;

ax.XGrid         = 'on';
ax.YGrid         = 'on';
ax.GridLineStyle = gridStyle;
ax.GridColor     = axisColor;
ax.YAxis.Exponent = 0;
lgd = legend([l1, l2, q],"Mean","Predicted","Launch Window");
lgd.FontSize = labelFont;




