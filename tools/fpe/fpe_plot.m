close all
clear
clc
set(0,'DefaultFigureWindowStyle','docked')

%% Load and Process
cd ..;
cd ..;
rocket = 'Theseus';
lSite  = 'IREC 2018';
zTitle = 'Apogee [ft]';
%zTitle =  'Apogee
pressFlag = 0;
printFlag = 0;
printDir = 'TBD';
figDir = 'TBD';
printTitle = 'mach_eta90';
files   = dir( '.\output\*.mat' );
eta = 1.00;
for i = 1:length(files)
    
    fileDir   = files(i).folder;
    fileName  = ['/' files(i).name];
    data      = load([fileDir fileName]);
    numMC  = data.inp.sim.numMC;
    massDry(i) = data.inp.bod.massDry;
%     tempTank(i)=data.inp.prp.tempTank;
    fillOx(i)=data.inp.prp.fillOx;
%     railElev(i) = data.inp.bod.railElev;
%     wind(i) = data.inp.atm.windAvg;
    mach(i) = mean(max(data.out.mach,[],1));
    for j = 1:numMC
        apg(j) = max(data.out.tPos(:,j,3));
    end
     apogee(i)=mean(apg);
end
% windVel = round(mean(wind));
% windStr = ['Wind Vel. ' num2str(windVel) ' [ft/s]'];

x=unique(massDry);
x=double(x);

%y = unique(tempTank);
y = unique(fillOx);
z = NaN(length(x),length(y));

for i = 1:numel(z)
    try
        ix = find(x == massDry(i));
        iy = find(y(i) == fillOx);
        iy = max(iy);
    catch
        continue
    end
    z(ix,i) = mach(iy); 
    
end
z=z';
%z(z==0)=NaN;
% z(isnan(z))=[];
%z=reshape(z,[length(y),length(x)])
nFact = 4;
xq    = linspace(x(1),x(end),nFact*length(x));
yq    = linspace(y(1),y(end),nFact*length(y));

[xM,yM] = meshgrid(x,y);
[xq,yq] = meshgrid(xq,yq);
%disp(z);
zq      = interp2(xM,yM,z,xq,yq);

if pressFlag % Temp to pressure conversion
    
    a1                     = 61.5168;
    a2                     = -2.1016E3;
    a3                     = -2.2337E1;
    a4                     = 1.8232E-2;
    a5                     = -1.1348E-10;
    F2K = @(T) (T+459.67)*(5/9);
    vaporPressure = @(T) ( 10.^( a1 + a2./T + a3.*log10(T) + a4.*T + a5.*T.^2 ).*133.322 ); % [Pa]
    P = @(T) vaporPressure(F2K(T)).*0.000145;
    yq = P(yq);
    
end

%% Plot 
%set(gca,'Color','red')
cStepApg = 3000:1000:30000;
cStepMach = 0:.05:2;
[c,h] = contourf(xq,yq,zq);

clabel(c,h,'FontSize',22)
set(gca,'Color','red')
% txt='OVERFILL';
% text(23,84,txt,'FontSize', 28)
grid on

ax = gca;
ax.FontSize = 26;
ax.GridAlpha = 0.75;
ax.FontName = 'Franklin Gothic Book';
ax.XMinorTick = 'on';
    ax.XMinorGrid = 'on';
ax.YMinorTick = 'on';
    ax.YMinorGrid = 'on';
if pressFlag 
    
    ylabel('Tank Press [psi]');
    
else
    
    ylabel('Oxidizer Fill [lb_m]');
    %ylabel('Tank Temperature [^o F]');
    
end
xlabel('Dry Weight [lb_m]')
%xlabel('Wind Vel. [ft/s]');
title([zTitle ' | ' rocket ' | ' lSite ' | \eta = ' num2str(eta) ],'FontSize',30)
if printFlag == 1
    print([printDir printTitle],'-dpng');
    savefig([figDir printTitle]);
end





