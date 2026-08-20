
clc; clear; close all;
set(0,'DefaultFigureWindowStyle','docked')
cd ../..;
saveFigs = true;
format = 'powerpoint';
file = "./tools/weather/hutto_weather_021619.csv";
data = readtable(file);


meanTemp = table2array(data(:,'HLY_TEMP_NORMAL'));
tenTemp = table2array(data(:,'HLY_TEMP_10PCTL'));
nineTemp = table2array(data(:,'HLY_TEMP_90PCTL'));

meanDew = table2array(data(:,'HLY_DEWP_NORMAL'));
tenDew = table2array(data(:,'HLY_DEWP_10PCTL'));
nineDew = table2array(data(:,'HLY_DEWP_90PCTL'));

wind = table2array(data(:,'HLY_WIND_AVGSPD'));
windDir = table2array(data(:,'HLY_WIND_VCTDIR'));



time = zeros(numel(data(:,'DATE')),1);
for i = 1:numel(time)
    time(i) = 6+i-1;
end
target = 14;
zoneSize = 1;
launchGoal = target-zoneSize:1:target+zoneSize;
launchIndex = launchGoal(:) - time(1)+1;

far2cel = @(T) (T - 32) .* (5/9);
humFun = @(T,TD) 100.*(exp((17.625.*TD)./(243.04+TD))./exp((17.625.*T)./(243.04+T)));
meanHum = humFun(far2cel(meanTemp),far2cel(meanDew));
tenHum = humFun(far2cel(tenTemp),far2cel(tenDew));
nineHum = humFun(far2cel(nineTemp),far2cel(nineDew));
hum = [tenHum meanHum nineHum];
 % Target index of 2PM
temp = [tenTemp meanTemp nineTemp];
wind = wind * 1.4667; % mph to ft/s
windDir = -90 + -windDir +360; %converts direction to fs frame
%windDir = 270 - windDir;

% Additional Calculations
meanT = mean(meanTemp(launchIndex));
stdDevTemp = (mean(nineTemp(launchIndex)) - meanT)/1.282;
fprintf("\nTemperature: %f +- %f ", meanT, stdDevTemp);

meanH= mean(meanHum(launchIndex));
stdDevHum = (mean(nineHum(launchIndex)) - meanH)/1.282;
fprintf("\nHumidity: %f +- %f ", meanH, stdDevHum);

fprintf("\nWind: %f +- %f ", mean(wind(launchIndex)), std(wind(launchIndex)));
fprintf("\nWind Direction: %f +- %f " , mean(windDir(launchIndex)), std(windDir(launchIndex)));

% THESE STATISTICS ARE ALL SORTS OF FUCKED UP! (but it'll fly for now)

%% Temperature
x = 0:0.1:200;
figure();
hold on;
for i = 1:numel(launchGoal)
    meanT(i) = meanTemp(launchIndex(i));
    stdDevTemp(i) = (mean(nineTemp(launchIndex)) - meanT(i))/1.282;
    normT(:,i) = 100*normpdf(x,meanT(i),stdDevTemp(i));
    p(i) = plot(x,normT(:,i));
    d = plot(ones(1,2)*meanT(i),[0,max(normT(:,i))], 'k', 'LineStyle', '--', 'Color', p(i).Color);
end
sumNormT = 100*normpdf(x,mean(meanT),mean(stdDevTemp));

p(end+1) = plot(x,sumNormT,'k');
plot(ones(1,2)*mean(meanT),[0,max(sumNormT)], 'k', 'LineStyle', '--');
xlabel('Temperature [ ^{o}F ]');
ylabel('Liklihood [ % ]');
xTxt = {"6:00", "7:00" , "8:00" "9:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"...
    "16:00", "17:00", "18:00", "19:00"};
title('Historical Temperature Profiles');
legend(p, [xTxt(launchIndex), {'Characteristic PDF'}]);
srt_fs_format(format);
ax = gca;
ax.XLim = [40, 85];

%% Humidity
clear p;
x = 0:0.1:100;
figure();
hold on;
for i = 1:numel(launchGoal)
    meanH(i) = meanHum(launchIndex(i));
    stdDevH(i) = (mean(nineHum(launchIndex)) - meanH(i))/1.282;
    normH(:,i) = 100*normpdf(x,meanH(i),stdDevH(i));
    p(i) = plot(x,normH(:,i));
    d = plot(ones(1,2)*meanH(i),[0,max(normH(:,i))], 'k', 'LineStyle', '--', 'Color', p(i).Color);
end
sumNormH = 100*normpdf(x,mean(meanH),mean(stdDevH));
p(end+1) = plot(x,sumNormH,'k');
% meanInd = 
plot(ones(1,2)*mean(meanH),[0,max(sumNormH)], 'k', 'LineStyle', '--');
xlabel('Humidity [ % ]');
ylabel('Liklihood [ % ]');
xTxt = {"6:00", "7:00" , "8:00" "9:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"...
    "16:00", "17:00", "18:00", "19:00"};
title('Historical Humidity Profiles');
legend(p, [xTxt(launchIndex), {'Characteristic PDF'}]);
srt_fs_format(format);
ax = gca;
ax.XLim = [20, 80];

%% Wind Velocity
figure()
hold on;
x = 10:0.01:15;
normW = 100*normpdf(x,mean(wind(launchIndex)),std(wind(launchIndex)));
plot(x,normW, 'k');
plot(ones(1,2)*mean(wind(launchIndex)),[0,max(normW)], 'k', 'LineStyle', '--');
xlabel('Wind Velocity [ ft/s ]');
ylabel('Liklihood [ % ]');
title('Historical Wind Velocity Profile');
legend({'Characteristic PDF'});
srt_fs_format(format);

%% Wind Direction
figure()
hold on;
x = 0:0.1:180;
normWd = 100*normpdf(x,mean(windDir(launchIndex)),std(windDir(launchIndex)));
plot(x,normWd, 'k');
plot(ones(1,2)*mean(windDir(launchIndex)),[0,max(normWd)], 'k', 'LineStyle', '--');
xlabel('Wind Direction [ deg CW from East ]');
ylabel('Liklihood [ % ]');
title('Historical Wind Direction Profile');
legend({'Characteristic PDF'});
srt_fs_format(format);
% minTemp = min(temp(:,1))
% meanTemp = mean(temp(:,2))
% maxTemp = max(temp(:,3))
% 
% 
% minHum = min(tenHum)
% meanHum = mean(meanHum)
% maxHum = max(nineHum)
% 
% minWind = min(wind)
% meanWind = mean(wind)
% maxWind = max(wind)
% stdWind = std(wind)
% 
% minWindDir = min(windDir)
% meanWindDir = mean(windDir)
% maxWindDir = max(windDir)
% stdWindDir = std(windDir)


%% plotting
lineWidth = 2;
date = 'Nov. 3rd';
figure()
%Humidity
hold on 
l1 = plot(time,hum(:,1),'Color','b','LineWidth', lineWidth);
l2 = plot(time,hum(:,2),'Color','k','LineWidth', lineWidth);
l3 = plot(time,hum(:,3),'Color','r','LineWidth', lineWidth);
%place holder plot for grey color
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
q = patch(qp,qp,grey);
q.EdgeColor = grey;
ylabel("Humidity [ % ]");
xlabel("Time");
ylim([0,100]);
xlim([time(1), time(end)]);
ax = gca;
xticks(time);
xTxt = {"6:00", "7:00" , "8:00" "9:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"...
    "16:00", "17:00", "18:00", "19:00"};
ax.XTickLabel = xTxt;
title(['Humidity | ' date '| 30 Year Normal']);
xP2  = [launchGoal flip(launchGoal)];
launchIndex = launchGoal(:) - time(1)+1;
% ind1 = launchGoal(1) -time(1)+1;
% ind2 = launchGoal(2) -time(1)+1;
% ind3 = launchGoal(3) -time(1)+1;
yP2 = [ hum(launchIndex(:),1);hum(launchIndex(length(launchIndex):-1:1),3)];
p2 = patch(xP2,yP2,1);
p2.FaceColor   = 'k';
p2.FaceAlpha   = 0.5 / 3;

lgd = legend([l3, l2, l1, q],"90th Percentile", "Mean", "10th Percentile","Launch Window");
srt_fs_format(format);
ax = gca;
ax.GridAlpha = 0.3;
ax.XMinorTick = 'off';
ax.YMinorTick = 'off';


%Temperature
figure()
hold on
l1=plot(time,temp(:,1),'Color','b','LineWidth', lineWidth);
l2=plot(time,temp(:,2),'Color','k','LineWidth', lineWidth);
l3=plot(time,temp(:,3),'Color','r','LineWidth', lineWidth);
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
q = patch(qp,qp,grey);
q.EdgeColor = grey;

ylabel("Temperature [ ^{o}F ]");
xlabel("Time");
ylim([min(temp(:,1))-10, max(temp(:,3))+10]);
xlim([time(1), time(end)]);
title(['Temperature | ' date '| 30 Year Normal']);

yP2 = [ temp(launchIndex(:),1);temp(launchIndex(length(launchIndex):-1:1),3)];
p2 = patch(xP2,yP2,1);
p2.FaceColor   = 'k';
p2.FaceAlpha   = 0.5 / 3;
ax             = gca;
xticks(time);
ax.XTickLabel = xTxt;
lgd = legend([l3, l2, l1, q],"90th Percentile", "Mean", "10th Percentile","Launch Window");
lgd.Location = 'best';
srt_fs_format(format);
ax = gca;
ax.GridAlpha = 0.3;
ax.XMinorTick = 'off';
ax.YMinorTick = 'off';



%Wind Direction
figure()
l1=plot(time,windDir,'Color','k','LineWidth',lineWidth);
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
q = patch(qp,qp,grey);
q.EdgeColor = grey;

ylabel("Wind Dir [ deg CW from East ]");
ylim([min(windDir) - 5, max(windDir) + 5]);
xlim([time(1), time(end)]);
xlabel("Time");
grid on;
title(['Wind Direction | ' date '| 30 Year Normal']);
yP2 = [min(windDir) - 5, max(windDir) + 5, max(windDir) + 5, min(windDir) - 5];
xP2 = [launchGoal(1), launchGoal(1), launchGoal(end) ,launchGoal(end)];
p2 = patch(xP2,yP2,1);
p2.FaceColor   = 'k';

p2.FaceAlpha   = 0.5 / 3;

ax                = gca;
xticks(time);
ax.XTickLabel = xTxt;
lgd = legend([l1,q],"Mean","Launch Window");
lgd.Location = 'best';
srt_fs_format(format);
ax = gca;
ax.GridAlpha = 0.3;
ax.XMinorTick = 'off';
ax.YMinorTick = 'off';


% Wind Speed
figure()
l1=plot(time,wind,'Color','k','LineWidth',lineWidth);
qp = [NaN,NaN,NaN,NaN];
grey = [0.5, 0.5, 0.5];
q = patch(qp,qp,grey);
q.EdgeColor = grey;

ylabel("Wind Speed [ ft/s ]");
ylim([0, 20]);
xlim([time(1), time(end)]);
xlabel("Time");
grid on;
title(['Wind Velocity | ' date '| 30 Year Normal']);
yP2 = [0, 20, 20, 0];
xP2 = [launchGoal(1), launchGoal(1), launchGoal(end) ,launchGoal(end)];
p2 = patch(xP2,yP2,1);
p2.FaceColor   = 'k';

p2.FaceAlpha   = 0.5 / 3;

ax                = gca;
xticks(time);
ax.XTickLabel = xTxt;
lgd = legend([l1,q],"Mean","Launch Window");
srt_fs_format(format);
ax.GridAlpha = 0.3;
ax.XMinorTick = 'off';
ax.YMinorTick = 'off';

figFormat = '-dpng';    % Save format
figOption = '-r300';
if saveFigs 
    myFigs = flip(get(0,'children'));
    figure(1)
    for i = 1:length(myFigs)
        print(myFigs(i),sprintf('./figures/%s',['figure' num2str(i)]),figFormat,figOption);
    end
end





