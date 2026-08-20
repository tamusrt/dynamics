close all
clear
clc
set(0,'DefaultFigureWindowStyle','docked')

%% Load and Process

rocket = 'Theseus';
lSite  = 'Hutto';
%zTitle = 'Apogee [ft]';
cd ..;
cd ..;


zTitle =  'Rail Exit [Mach #]';
printFlag = 0;
% printDir = 'C:\Users\jakemantiv\Desktop\dev_caesar\fs_pdr\fs\figs\';
% figDir = 'C:\Users\jakemantiv\Desktop\dev_caesar\srt_fs\figures\fpes\figs\';
printTitle = 'rail_exit';
files   = dir( './output/*.mat' );
eta = .72;
for i = 1:length(files)
    
    fileDir   = files(i).folder;
    fileName  = ['/' files(i).name];
    data      = load([fileDir fileName]);
    numMC  = data.inp.sim.numMC;
    presTank(i)=data.inp.prp.presTank;
    fillOx(i)=data.inp.prp.fillOx;
    for j = 1:numMC
        apg(j) = max(data.out.tPos(:,j,3));
%         machnum(j) = max(data.out.mach(:,j));
    end
    apogee(i)=mean(apg);
%     mach(i)=mean(machnum);
%     tempTank(i)   = data.inp.prp.tempTank; 
%     fillOx(i) = data.inp.prp.fillOx;
    for j = 1:data.inp.sim.numMC
    posMag = sqrt(sum(data.out.tPos.^2,3));
%     velMag = sqrt(sum(data.out.tVel.^2,3));
    ind    = find(posMag(:,j) > ...
             (data.inp.bod.railLength + (data.inp.bod.length - data.out.xCG)),1,'first');
    summ.railExitmach(j) = data.out.mach(ind,j);
    end
    railExitmach(i) = mean(summ.railExitmach);
end



x=unique(fillOx);
x=double(x);

y = unique(presTank);
z = NaN(length(x),length(y));

for i = 1:numel(z)
    try

        ix = find(x == fillOx(i));
        iy = find(y == presTank(i));
    catch
        continue
    end
    z(ix,iy) = railExitmach(i);   
end
z = z';
%z=(isnan(z))=[];
% z(end,:)=[];
% z=reshape(z,[length(y),length(x)])
% z(:,length(x)+1:size(z,2))=[];
nFact = 4;
xq    = linspace(x(1),x(end),nFact*length(x));
yq    = linspace(y(1),y(end),nFact*length(y));

[xM,yM] = meshgrid(x,y);
[xq,yq] = meshgrid(xq,yq);
zq      = interp2(xM,yM,z,xq,yq);

%% Plot 
cStepApg = 2000:1000:20000;
cStepMach = 0:.002:.1; %bounds of red/contourf
cStepVel = 0:1:150;
[c,h] = contourf(xq,yq,zq,cStepMach);
colormap(flipud(summer));
hold on;
% plot([15, 25], [750, 850], 'Color', 'k', 'LineStyle','--') %line on plot
clabel(c,h,'FontSize',22)
set(gca,'Color',[.68 .1 .1])
txt='OVERFILL';
text(25,965,txt,'FontSize', 28)
grid on

ax = gca;
ax.FontSize = 28;
ax.GridAlpha = 0.75;
ax.FontName = 'Franklin Gothic Book';
ax.XMinorTick = 'on';
    ax.XMinorGrid = 'on';
ax.YMinorTick = 'on';
    ax.YMinorGrid = 'on';
    
ylabel('Tank Pressure [psi]')
xlabel('N20 fill [lb_m]')
title([zTitle ' | ' rocket  ' | ' lSite ' | ' num2str(numMC) ' MC'] ,'FontSize',32)
srt_fs_format('powerpoint');
if printFlag == 1
    print([printDir printTitle],'-dpng');
    %savefig([figDir printTitle]);
end





