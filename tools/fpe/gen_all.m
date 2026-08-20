% This script generates the following FPE plots:
% - Stability margin (Wind velocity over tank pressure)
% - Rail exit (Tank pressure over nitrous fill)

% ================================= User configuration
%%% Base config to override (searches in '<fs-root>/input/*.inp')
base_cfg = "hearne_10k_21";

rocket = 'Lazarus';
lSite  = 'Hearne';
numMC = 1;

%% Plotting variables
% make color map for plots
map_red_to_yellow = [ones(500, 1) linspace(0, 1, 500)' zeros(500, 1)];
map_yellow_to_green = [linspace(1,0,500)' ones(500, 1) zeros(500, 1)];
map_green_to_yellow = [linspace(0,1,500)' ones(500, 1) zeros(500, 1)];
map_yellow_to_red = [ones(500, 1) linspace(1, 0, 500)' zeros(500, 1)];
map = cat(1, map_red_to_yellow, map_yellow_to_green, map_green_to_yellow, map_yellow_to_red);
% create step for plotting lines of z values
cStepStab = 0:.05:3;
cStepApg = 0:1000:30000;
stablimits = [1.4, 2.1];
apglimits = [7000, 13000];

%%% Ranges. Change these if needed.
% Fill [lb]
fill_min = 12;
fill_max = 24;
fill_resolution = 2;
% Wind speed [ft/s]
wind_min = 0;
wind_max = 30;
wind_resolution = 2;
% Tank pressure [psi]
press_min = 500;
press_max = 900;
press_resolution = 20;
% Tank fill [ for sm plot]
% Need to generate this multiple times for different fills
sm_fill_target = 16;
sm_wind_target = 10; %ft/s
sm_tank_press = 750;
%/================================= end User configuration

%%% Prepare simulation inputs
% wind vs press plot input space
[wind_pressure_tank_press, wind_pressure_wind_vel] = meshgrid(press_min:press_resolution:press_max, wind_min:wind_resolution:wind_max);

% fill vs press plot input space
[fill_pressure_fill, fill_pressure_tank_press] = meshgrid(fill_min:fill_resolution:fill_max, press_min:press_resolution:press_max);

% fill vs wind plot input space
[fill_wind_fill, fill_wind_wind] = meshgrid(fill_min:fill_resolution:fill_max, wind_min:wind_resolution:wind_max);

fprintf("Combined input space: %i simulations\n", numel(wind_pressure_tank_press) + numel(fill_pressure_fill) + numel(fill_wind_fill));

%%% Run simulations using iOverride


%<<<<<<< Updated upstream
cd ..;
cd ..;

%% Wind vs Pressure
fprintf("Running simulations for wind vs pressure plot...\n");

% stability variable holder:
SM_1_out = zeros(size(wind_pressure_tank_press));
% apogee variable holder
Apogee_1_out = zeros(size(wind_pressure_tank_press));

parfor idx = 1:numel(wind_pressure_tank_press)
% >>>>>>> Stashed changes
    sim_tank_press = wind_pressure_tank_press(idx);
    sim_wind_vel = wind_pressure_wind_vel(idx);
    fprintf("(press, vel, idx)=(%f, %f, %i)\n", sim_tank_press, sim_wind_vel, idx);
    
    % Construct override cell array
    overrides = {
        "prp.presTank", sim_tank_press, ...
        "prp.fillOx"  , sm_fill_target, ...
        "atm.windAvg" , sim_wind_vel ...
    };

    [~, out] = srt_fs_main(base_cfg, ...
        'numMC', 1, ...
        'numThreads', 1, ...
        'genOutput', true, ...
        'iOverride', overrides);

    % cut to only look at first half to get rail exit and neglect rest of
    % flight
    indRE_1 = abs(out.tPos(:, :, 3) -30);
    [~,indRE] = min(abs(indRE_1(1:length(out.tPos(:,:,3))/2, 1)-30)); %finds index/time of rail exit

    % get min stability
    SM_1_out(idx) = min(out.stab(indRE:length(out.stab)/2));

    % get apogee
    Apogee_1_out(idx) = max(out.tPos(:,:,3));
end

nFact = 4;
wind_pressure_xq    = linspace(press_min,press_max,nFact*(((press_max-press_min)/press_resolution)+1));
wind_pressure_yq    = linspace(wind_min,wind_max,nFact*(((wind_max-wind_min)/wind_resolution)+1));

[wind_pressure_xq,wind_pressure_yq] = meshgrid(wind_pressure_xq,wind_pressure_yq);
wind_pressure_stab      = interp2(wind_pressure_tank_press, wind_pressure_wind_vel, SM_1_out,wind_pressure_xq, wind_pressure_yq);
wind_pressure_apogee      = interp2(wind_pressure_tank_press, wind_pressure_wind_vel, Apogee_1_out, wind_pressure_xq, wind_pressure_yq);

%% Plot Stability

% create figure object
figure(1);

% set titles
zTitle =  'Minimum Stabilty Margin [cal]';

% apply color gradient
pcolor(wind_pressure_xq, wind_pressure_yq, wind_pressure_stab);
shading interp;
colormap(map);
caxis(stablimits); % set limits

% make grid on top of colormap
grid on;
set(gca, 'Layer', 'top');
set(gca, 'Color', 'k');

% hold to overlay contour
hold on;

% contour plot, c is the contour matrix and h is the contour object
[c,h] = contour(wind_pressure_xq,wind_pressure_yq,wind_pressure_stab,cStepStab);
 
%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [press_min, press_max];

% set axis title and chart title
ylabel('Wind Vel. [ft/s]')
xlabel('Tank Pressure [psi]')
title([zTitle ' | ' rocket ' | ' lSite newline num2str(sm_fill_target) 'lb Fill'],'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');

%% Plot Apogee 

% create figure object
figure(2);

% set title
zTitle =  'Apogee [ft AGL]';

% apply color gradient
pcolor(wind_pressure_xq, wind_pressure_yq, wind_pressure_apogee);
shading interp;
colormap(map);
caxis(apglimits);

% make grid on top of colormap
grid on;
set(gca, 'Layer', 'top');
set(gca, 'Color', 'k');

% hold to overlay contour
hold on;

% contour plot, c is the contour matrix and h is the contour object
[c,h] = contour(wind_pressure_xq,wind_pressure_yq,wind_pressure_apogee,cStepApg);

%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [press_min, press_max];

% set axis and chart title
ylabel('Wind Vel. [ft/s]')
xlabel('Tank Pressure [psi]')
title([zTitle ' | ' rocket  ' | ' lSite newline num2str(sm_fill_target) 'lb Fill'] ,'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');



%% Pressure vs Fill
fprintf("Running simulations for fill vs pressure plot...\n");

% stability variable holder:
SM_2_out = zeros(size(fill_pressure_tank_press));
% apogee variable holder
Apogee_2_out = zeros(size(fill_pressure_tank_press));

parfor idx = 1:numel(fill_pressure_tank_press)
    sim_tank_press = fill_pressure_tank_press(idx);
    sim_fill = fill_pressure_fill(idx);
    fprintf("(press, fill, idx)=(%f, %f, %i)\n", sim_tank_press, sim_fill, idx);
    
    % Construct override cell array
    overrides2 = {
        "prp.presTank", sim_tank_press, ...
        "prp.fillOx"  , sim_fill, ...
        "atm.windAvg" , sm_wind_target ...
    };

    [~, out2] = srt_fs_main(base_cfg, ...
        'numMC', 1, ...
        'numThreads', 1, ...
        'genOutput', true, ...
        'iOverride', overrides2);
    
    % cut to only look at first half to get rail exit and neglect rest of
    % flight
    indRE_1 = abs(out2.tPos(:, :, 3) -30);
    [~,indRE] = min(abs(indRE_1(1:length(out2.tPos(:,:,3))/2, 1)-30)); %finds index/time of rail exit

    % get min stability
    SM_2_out(idx) = min(out2.stab(indRE:length(out2.stab)/2));

    % get apogee
    Apogee_2_out(idx) = max(out2.tPos(:,:,3));
    
end

nFact = 4;
fill_pressure_xq    = linspace(fill_min,fill_max,nFact*(((fill_max-fill_min)/fill_resolution)+1));
fill_pressure_yq    = linspace(press_min,press_max,nFact*(((press_max-press_min)/press_resolution)+1));

[fill_pressure_xq,fill_pressure_yq] = meshgrid(fill_pressure_xq,fill_pressure_yq);
fill_pressure_stab      = interp2(fill_pressure_fill, fill_pressure_tank_press, SM_2_out, fill_pressure_xq, fill_pressure_yq);
fill_pressure_apogee      = interp2(fill_pressure_fill, fill_pressure_tank_press, Apogee_2_out, fill_pressure_xq, fill_pressure_yq);

%% Plot Stability

% create figure object
figure(3);

% set titles
zTitle =  'Minimum Stabilty Margin [cal]';

% apply color gradient
pcolor(fill_pressure_xq, fill_pressure_yq, fill_pressure_stab);
shading interp;
colormap(map);
caxis(stablimits); % set limits

% make grid on top of colormap
grid on;
set(gca, 'Layer', 'top');
set(gca, 'Color', 'k');

% hold to overlay contour
hold on;

% contour plot, c is the contour matrix and h is the contour object
[c,h] = contour(fill_pressure_xq,fill_pressure_yq,fill_pressure_stab,cStepStab);
 
%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [fill_min, fill_max];

% set axis title and chart title
ylabel('Tank Pressure [psi]')
xlabel('N20 fill [lb_m]')
title([zTitle ' | ' rocket ' | ' lSite newline num2str(sm_wind_target) 'ft/s Wind Vel'],'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');

%% Plot Apogee 

% create figure object
figure(4);

% set title
zTitle =  'Apogee [ft AGL]';

% apply color gradient
pcolor(fill_pressure_xq, fill_pressure_yq, fill_pressure_apogee);
shading interp;
colormap(map);
caxis(apglimits);

% make grid on top of colormap
grid on;
set(gca, 'Layer', 'top');
set(gca, 'Color', 'k');

% hold to overlay contour
hold on;

% contour plot, c is the contour matrix and h is the contour object
[c,h] = contour(fill_pressure_xq,fill_pressure_yq,fill_pressure_apogee,cStepApg);

%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [fill_min, fill_max];

% set axis and chart title
ylabel('Tank Pressure [psi]')
xlabel('N20 fill [lb_m]')
title([zTitle ' | ' rocket  ' | ' lSite newline num2str(sm_wind_target) 'ft/s Wind Vel'] ,'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');


%% Fill vs Wind
fprintf("Running simulations for fill vs wind plot...\n");

% stability variable holder:
SM_3_out = zeros(size(fill_wind_fill));
% apogee variable holder
Apogee_3_out = zeros(size(fill_wind_fill));

parfor idx = 1:numel(fill_wind_fill)
    sim_fill = fill_wind_fill(idx);
    sim_wind = fill_wind_wind(idx);
    fprintf("(find, wind_vel, idx)=(%f, %f, %i)\n", sim_fill, sim_wind, idx);
    
    % Construct override cell array
    overrides3 = {
        "prp.presTank", sm_tank_press, ...
        "prp.fillOx"  , sim_fill, ...
        "atm.windAvg" , sim_wind ...
    };

    [~, out3] = srt_fs_main(base_cfg, ...
        'numMC', 1, ...
        'numThreads', 1, ...
        'genOutput', true, ...
        'iOverride', overrides3);

    % cut to only look at first half to get rail exit and neglect rest of
    % flight
    indRE3_1 = abs(out3.tPos(:, :, 3) -30);
    [~,indRE3] = min(abs(indRE3_1(1:length(out3.tPos(:,:,3))/2, 1)-30)); %finds index/time of rail exit

    % get min stability
    SM_3_out(idx) = min(out3.stab(indRE3:length(out3.stab)/2));

    % get apogee
    Apogee_3_out(idx) = max(out3.tPos(:,:,3));
    
end

nFact = 4;
fill_wind_xq    = linspace(fill_min,fill_max,nFact*(((fill_max-fill_min)/fill_resolution)+1));
fill_wind_yq    = linspace(wind_min,wind_max,nFact*(((wind_max-wind_min)/wind_resolution)+1));

[fill_wind_xq,fill_wind_yq] = meshgrid(fill_wind_xq,fill_wind_yq);
fill_wind_stab      = interp2(fill_wind_fill, fill_wind_wind, SM_3_out, fill_wind_xq, fill_wind_yq);
fill_wind_apogee      = interp2(fill_wind_fill, fill_wind_wind, Apogee_3_out, fill_wind_xq, fill_wind_yq);

%% Plot Stability

% create figure object
figure(5);

% set titles
zTitle =  'Minimum Stabilty Margin [cal]';

% apply color gradient
pcolor(fill_wind_xq, fill_wind_yq, fill_wind_stab);
shading interp;
colormap(map);
caxis(stablimits); % set limits

% make grid on top of colormap
grid on;
set(gca, 'Layer', 'top');
set(gca, 'Color', 'k');

% hold to overlay contour
hold on;

% contour plot, c is the contour matrix and h is the contour object
[c,h] = contour(fill_wind_xq,fill_wind_yq,fill_wind_stab,cStepStab);
 
%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [fill_min, fill_max];

% set axis title and chart title
ylabel('Wind Vel. [ft/s]')
xlabel('N20 fill [lb_m]')
title([zTitle ' | ' rocket ' | ' lSite newline num2str(sm_tank_press) 'psi Tank Press'],'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');

%% Plot Apogee 

% create figure object
figure(6);

% set title
zTitle =  'Apogee [ft AGL]';

% apply color gradient
p = pcolor(fill_wind_xq, fill_wind_yq, fill_wind_apogee);
shading interp;
colormap(map);
caxis(apglimits);

% make grid on top of colormap
grid on;
set(gca, 'Layer', 'top');
set(gca, 'Color', 'k');

% hold to overlay contour
hold on;

% contour plot, c is the contour matrix and h is the contour object
[c,h] = contour(fill_wind_xq,fill_wind_yq,fill_wind_apogee,cStepApg);

%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [fill_min, fill_max];

% set axis and chart title
ylabel('Wind Vel. [ft/s]')
xlabel('N20 fill [lb_m]')
title([zTitle ' | ' rocket  ' | ' lSite newline num2str(sm_tank_press) 'psi Tank Press'] ,'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');

