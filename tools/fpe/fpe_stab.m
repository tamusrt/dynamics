close all
clear
clc
set(0,'DefaultFigureWindowStyle','docked')

%% Load and Process
cd ..;
cd ..;
rocket = 'Theseus';
lSite  = 'Hutto';
%zTitle = 'Apogee [ft]';
zTitle =  'Minimum Stabilty Margin [cal]';
printFlag = 0;
% printDir = 'C:\Users\jakemantiv\Desktop\dev_caesar\fs_pdr\fs\figs\';
% figDir = 'C:\Users\jakemantiv\Desktop\dev_caesar\srt_fs\figures\fpes\figs\';
printTitle = 'stab_90';
files   = dir('./output/*.mat');
eta = .72;
for i = 1:length(files)
    fileDir   = files(i).folder;
    fileName  = ['/' files(i).name];
    data      = load([fileDir fileName]);
    numMC  = data.inp.sim.numMC;
    tempTank=data.inp.prp.tempTank;
    windAvg(i)=data.inp.atm.windAvg;
    fillOx(i)=data.inp.prp.fillOx;
    presTank(i) = data.inp.prp.presTank;
    %railElev(i) = data.inp.bod.railElev;
%     for j = 1:numMC
%         apg(j) = max(data.out.tPos(:,j,3));
%         machnum(j) = max(data.out.mach(:,j));
%     end
%     apogee(i)=mean(apg);
%     mach(i)=mean(machnum);
%     tempTank(i)   = data.inp.prp.tempTank; 
%     fillOx(i) = data.inp.prp.fillOx;
    stab=min(data.out.stab);
    minStab(i)=mean(stab);
    
end


disp(presTank);
%x=[unique(fillOx) 26 28];
x=unique(presTank);
x=double(x);

y = unique(windAvg);
z = NaN(length(x),length(y));

for i = 1:numel(z)
    try

        ix = find(x == presTank(i));
        iy = find(y == windAvg(i));
    catch
        continue
    end
    z(ix,iy) = minStab(i);
    
end
%z(z==0)=NaN;
%z(isnan(z))=0;
%z=z(1:end-1,1:end-1);
%z(:,end)=[];
%z=reshape(z,[length(y),length(x)])
%z(:,length(x)+1:size(z,2))=[];
%z(end,:)=[];
%z(:,end)=[];
z=z'
nFact = 4;
xq    = linspace(x(1),x(end),nFact*length(x));
yq    = linspace(y(1),y(end),nFact*length(y));

[xM,yM] = meshgrid(x,y);
[xq,yq] = meshgrid(xq,yq);
zq      = interp2(xM,yM,z,xq,yq);
zq(zq<.75)=NaN;
%% Plot 
%set(gca,'Color','red')
cStepApg = 3000:1000:30000;
cStepMach = 0:.05:2;
cStepStab = 0:.05:2;
[c,h] = contourf(xq,yq,zq,cStepStab);
colormap(flipud(summer));
clabel(c,h,'FontSize',22)
set(gca,'Color',[.68 .1 .1])
txt='S.M. < 0.75';
text(550,33,txt,'FontSize', 38)
%text(24.5,20,'OVERFILL','FontSize', 38)
grid on
xP1 = [ones(1,2).*18.5 ones(1,2).*19.5];
y_Plim = [max(y) min(y)];
yP1 = [y_Plim flip(y_Plim)];
p1  = patch(xP1,yP1,1);
p1.FaceColor   = 'k';
p1.EdgeColor   = 'k';
p1.FaceAlpha   = 0.225;
ax = gca;
ax.XLim = [min(x), max(x)];
ax.FontSize = 28;
ax.GridAlpha = 0.75;
ax.FontName = 'Franklin Gothic Book';
ax.XMinorTick = 'on';
    ax.XMinorGrid = 'on';
ax.YMinorTick = 'on';
    ax.YMinorGrid = 'on';
% leg=legend(p1,'Nominal Flight - 10k');
ylabel('Wind Vel. [ft/s]')
xlabel('Tank Pressure [psi]')
title([zTitle ' | ' rocket ' | ' lSite ' | ' num2str(numMC) ' MC'],'FontSize',32)
srt_fs_format('powerpoint');
if printFlag == 1
    print([printDir printTitle],'-dpng');
    %savefig([figDir printTitle]);
end





