% This script generates the following FPE plots:
% - Stability margin 
% - Apogee
base_cfg = "telemachus_hearne_feb2024";

rocket = 'Telemachus';
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
cStepApg = 0:250:13000;
stablimits = [1.5, 2.5];
apglimits = [7000, 13000];

%%% Ranges. Change these if needed.
% Wind speed [ft/s]
wind_min = 0;
wind_max = 30;
wind_resolution = 2;
% Overall Weight [lb_m]
mass_min = 40;
mass_max = 50;
mass_resolution = 2;

sm_wind_target = 10; %ft/s
%/================================= end User configuration

%%% Prepare simulation inputs
% wind vs press plot input space
[wind_mass_mass, wind_mass_wind_vel] = meshgrid(mass_min:mass_resolution:mass_max, wind_min:wind_resolution:wind_max);


%%% Run simulations using iOverride



cd ..;
cd ..;

%% Wind vs Pressure
fprintf("Running simulations for wind vs mass plot...\n");

% stability variable holder:
SM_1_out = zeros(size(wind_mass_mass));
% apogee variable holder
Apogee_1_out = zeros(size(wind_mass_mass));

parfor idx = 1:numel(wind_mass_mass)
% >>>>>>> Stashed changes
    sim_mass = wind_mass_mass(idx);
    sim_wind_vel = wind_mass_wind_vel(idx);
    fprintf("(press, vel, idx)=(%f, %f, %i)\n", sim_mass, sim_wind_vel, idx);
    
    % Construct override cell array
    overrides = {
        "bod.massDry", sim_mass, ...
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
wind_mass_xq    = linspace(mass_min,mass_max,nFact*(((mass_max-mass_min)/mass_resolution)+1));
wind_mass_yq    = linspace(wind_min,wind_max,nFact*(((wind_max-wind_min)/wind_resolution)+1));

[wind_mass_xq,wind_mass_yq] = meshgrid(wind_mass_xq,wind_mass_yq);
wind_mass_stab      = interp2(wind_mass_mass, wind_mass_wind_vel, SM_1_out,wind_mass_xq, wind_mass_yq);
wind_mass_apogee      = interp2(wind_mass_mass, wind_mass_wind_vel, Apogee_1_out, wind_mass_xq, wind_mass_yq);

%% Plot Stability

% create figure object
figure(1);

% set titles
zTitle =  'Minimum Stabilty Margin [cal]';

% apply color gradient
pcolor(wind_mass_xq, wind_mass_yq, wind_mass_stab);
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
[c,h] = contour(wind_mass_xq,wind_mass_yq,wind_mass_stab,cStepStab);
 
%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [mass_min, mass_max];

% set axis title and chart title
ylabel('Wind Vel. [ft/s]')
xlabel('Mass [lb_m]')
title([zTitle ' | ' rocket ' | ' lSite],'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');

%% Plot Apogee 

% create figure object
figure(2);

% set title
zTitle =  'Apogee [ft AGL]';

% apply color gradient
pcolor(wind_mass_xq, wind_mass_yq, wind_mass_apogee);
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
[c,h] = contour(wind_mass_xq,wind_mass_yq,wind_mass_apogee,cStepApg);

%label each line of the plot
clabel(c,h,'FontSize',22);

% set contour lines to black
h.LineColor = 'k';

% set limits of axes
ax = gca;
ax.XLim = [mass_min, mass_max];

% set axis and chart title
ylabel('Wind Vel. [ft/s]')
xlabel('Mass [lb_m]')
title([zTitle ' | ' rocket  ' | ' lSite] ,'FontSize',32)

% format and print plot
srt_fs_format('powerpoint');
