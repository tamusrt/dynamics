%% bisection calc
clear; clc;
targetApg = 5000;
tFlight = 35;
numIter = 1;
cd ..;
baseFile  = './input/test.inp';
numMC = 15;
error   = 500; 
fill(1) =11;
fill(2)= 18;
fillL  = fill(1);
fillU  = fill(2);
out   = getApg(baseFile,fillL,1,25); % output apgMean in srt_fs_main
apg(1) = out{1};
alt(1) = out(2);
out = getApg(baseFile,fillU,1,65);
apg(2) = out{1};
alt(2) = out(2);
apgL = apg(1);
apgU = apg(2);
for i = 3:3+numIter
    fill(i) = ((apgU-targetApg)*fillL-(apgL-targetApg)*fillU)/(apgU-apgL);
    out = getApg(baseFile,fill(i),numMC,tFlight);
    apg(i) = out{1};
    alt(i) = out(2);
    if abs(apg(i)-targetApg)<error % Was guess within error bound?
        break; % target fill found
    elseif sign(apg(i) - targetApg) == 1 % Was guess above target?
        fillU = fill(i); % Guess ---> new upper
        apgU = apg(i);
    elseif sign(apg(i) - targetApg) == -1 % Was guess below target?
        fillL = fill(i); % Guess ---> new lower
        apgL  = apg(i);
    end
end

%% Altitude vs. Nitrous

numsMC     = [num2str(numMC) ' MC'];
iteration = [num2str(numel(apg)) ' Iterations'];
method    = 'Regula Falsi';
titleFont = 34;
labelFont = 28;
axisWidth = 1.5;
axisColor = 'k';
gridAlpha = 0.5;
gridStyle = '-';
figure()
ax                = gca;
ax.FontName       = 'Franklin Gothic Book';
ax.XColor         = axisColor;
ax.YColor         = axisColor;
ax.FontSize       = labelFont;
ax.LineWidth      = axisWidth;
ax.Title.FontSize = titleFont;

ax.XGrid         = 'on';
ax.YGrid         = 'on';
ax.GridAlpha     = gridAlpha;
ax.GridLineStyle = gridStyle;
ax.GridColor     = axisColor;
ax.YAxis.Exponent = 0;
hold on 
s1=scatter(fill(1:end-1),apg(1:end-1),'filled');
s2 = plot(fill(end),apg(end),'p');
s2.MarkerFaceColor='r';
s2.MarkerSize=15;
s1.SizeData=100;
for i = 1:numel(fill)
    txt = [num2str(i)];
    text(fill(i),apg(i)+400, txt,'FontWeight','bold','FontSize', 22);
end
xP1 = [ones(1,2).*ax.XLim(1) ones(1,2).*ax.XLim(2)];
y_Plim = [targetApg+error targetApg-error];
yP1 = [y_Plim flip(y_Plim)];
p1  = patch(xP1,yP1,1);
p1.FaceColor   = [.5 .5 .5];
p1.EdgeColor   = 'k';
p1.FaceAlpha   = 0.3;
title(['Altitude vs Nitrous Fill' ' | ' method ' | ' iteration ' | ' numsMC],'FontWeight','bold','Color',axisColor)
ylabel('Alitude [ft]','FontWeight','bold','FontSize', labelFont)
xlabel('Nitrous Fill [lb_m]','FontWeight','bold','FontSize', labelFont)
legTxt1= [num2str(targetApg) '\pm ' num2str(error) ' [ft]'];
legTxt2 = ['Apogee - ' num2str(round(apg(end),0)) ' [ft], Nitrous Fill - ' num2str(round(fill(end),2)) ' [lb_m]'];
leg = legend([p1 s2],legTxt1,legTxt2);
leg.Location='northwest';

%% Altitude vs. Iteration
figure()
ax                = gca;
ax.FontName       = 'Franklin Gothic Book';
ax.XColor         = axisColor;
ax.YColor         = axisColor;
ax.FontSize       = labelFont;
ax.LineWidth      = axisWidth;
ax.Title.FontSize = titleFont;

ax.XGrid         = 'on';
ax.YGrid         = 'on';
ax.GridAlpha     = gridAlpha;
ax.GridLineStyle = gridStyle;
ax.GridColor     = axisColor;


hold on 
s1=scatter(1:numel(apg)-1,apg(1:end-1),'filled');
s2 = plot(numel(apg), apg(end),'p');
s3 = plot(1:numel(apg),apg,'--', 'Color', 'k', 'LineWidth', 3.5);
s2.MarkerFaceColor='r';
s2.MarkerSize=35;
s1.SizeData=120;
for i = 1:numel(fill)-1
    txt = sprintf('%2.2f [lb_m]', fill(i));
    text(i-0.55,apg(i)+400, txt,'FontWeight','bold','FontSize', 25);
end

txt = sprintf('%2.2f [lb_m]', fill(end));
text(numel(apg)-0.3, apg(end)+1200,txt,'FontWeight','bold','FontSize', 25);

ax.XLim = [0.3 numel(fill)+0.3];
ax.YLim = [0, max(apg)+2000];
ax.XTick = [1:numel(apg)];
xP1 = [ones(1,2).*ax.XLim(1) ones(1,2).*ax.XLim(2)];
y_Plim = [targetApg+error targetApg-error];
yP1 = [y_Plim flip(y_Plim)];
p1  = patch(xP1,yP1,1);
p1.FaceColor   = [.4 .4 .4];
p1.EdgeColor   = 'k';
p1.FaceAlpha   = 0.3;
title(['Altitude Convergence' ' | ' method ' | ' iteration ' | ' numsMC],'FontWeight','bold','Color',axisColor)
ylabel('Alitude [ft]','FontWeight','bold','FontSize', labelFont)
xlabel('Iteration','FontWeight','bold','FontSize', labelFont)
legTxt1= [num2str(targetApg) '\pm ' num2str(error) ' [ft]'];
legTxt2 = ['Apogee - ' num2str(round(apg(end),0)) ' [ft], Nitrous Fill - ' num2str(round(fill(end),2)) ' [lb_m]'];
leg = legend([p1 s2],legTxt1,legTxt2);
leg.Location='southeast';
ax.YAxis.Exponent = 0;
box on


%% Altitude vs. Time
[~,iApg] = max(alt{end}(:,2));
[~,iTraj] = sort(apg);
[~,iTraj] = sort(iTraj);
lnColor   = winter(numel(apg));

figure
ax                = gca;
ax.FontName       = 'Franklin Gothic Book';
ax.XColor         = axisColor;
ax.YColor         = axisColor;
ax.FontSize       = labelFont;
ax.LineWidth      = axisWidth;
ax.Title.FontSize = titleFont;

ax.XGrid         = 'on';
ax.YGrid         = 'on';
ax.GridAlpha     = gridAlpha;
ax.GridLineStyle = gridStyle;
ax.GridColor     = axisColor;
hold on
for i = 1:numel(apg)
    
    h(i) = plot(alt{i}(1:min(numel(alt{i}(:,1)),iApg),1),alt{i}(1:min(numel(alt{i}(:,1)),iApg),2),'LineWidth', 3,'Color', lnColor(iTraj(i),:));

end


% Plot labels
title(['Altitude Spread | ' num2str(numMC) ' MC'],'FontSize',titleFont,'Color',axisColor)
xlabel('Time [s]','FontSize',labelFont,'FontWeight','bold')
ylabel('Altitude [ft AGL]','FontSize',labelFont,'FontWeight','bold')


% Axes options
ax.XLim = [0 alt{end}(iApg,1)];
ax.YLim = [0 21000];
xP1 = [ones(1,2).*ax.XLim(1) ones(1,2).*ax.XLim(2)];
y_Plim = [targetApg+error targetApg-error];
yP1 = [y_Plim flip(y_Plim)];
p1  = patch(xP1,yP1,1);
p1.FaceColor   = [.5 .5 .5];
p1.EdgeColor   = 'k';
p1.FaceAlpha   = 0.3;
title(['Iteration: Target Oxidizer' ' | ' method ' | ' iteration ' | ' numsMC],'FontWeight','bold','Color',axisColor)
ylabel('Alitude [ft]','FontWeight','bold','FontSize', labelFont)
xlabel('Time [s]','FontWeight','bold','FontSize', labelFont)
legTxt{1} = [num2str(targetApg) '\pm ' num2str(error) ' [ft]'];
for i = 1:numel(apg)
    legTxt{i+1} = sprintf('%2.2f [lb_m]', fill(i));
end
legTxt1= [num2str(targetApg) '\pm ' num2str(error) ' [ft]'];
legTxt2 = ['Apogee - ' num2str(round(apg(end),0)) ' [ft], Nitrous Fill - ' num2str(round(fill(end),2)) ' [lb_m]'];
leg = legend([p1, h], legTxt);
leg.Location='northwest';
box on;
ax.YAxis.Exponent = 0;





%% input file manipulation

function [out] = getApg(baseFile,fillOx,numMC,timeFlight) 
    fid = fopen(baseFile);
    baseCell      = textscan(fid,'%s','Delimiter','');
    inputCell     = baseCell{1};
    for j = 1:length(inputCell)
        if strfind(inputCell{j},'fillOx')
            txt = sprintf('\tfillOx = %d;',fillOx);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'numMC')
            txt = sprintf('\tnumMC = %i;',numMC);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'timeFlight')
            txt = sprintf('\ttimeFlight = %d;',timeFlight);
            inputCell{j} = txt;
%             break;
        end
    end
    exportFile = baseFile;
    fid = fopen(exportFile,'w');
    fprintf(fid,'%s\n',inputCell{:});
    out = srt_fs_main(baseFile);
end

    
    



