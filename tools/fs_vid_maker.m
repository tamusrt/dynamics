clear; close all; clc

%% Import an FS output file from which the video will be made. 
%---------------------------------------------------------------------------
tic
[fileName,fileDir,~] = uigetfile('../output/*.mat*','Select Output File');
filePath = [fileDir fileName];
load(filePath)
vidName = [fileName(1:(end-4))];                % Assign video name to correspond with chosen .mat file. 
                                                %   The index range 1:(end-4) deletes the file extension ".mat" 
                                                %   from the name string.
vidType = '.mp4';                               % Specify video file type. Options are:
                                                    % .avi (Motion JPEG AVI)
                                                    % .mj2 (Motion JPEG 2000)
                                                    % .mp4 (Windows 7 or later)
                                                    % .m4v (Mac OS X 10.7 or later)

vidProfile = 'MPEG-4';                % Modify vidType with a specific profile. Options are as follows:
                                                    % For .avi files:
                                                        % 'Motion JPEG AVI' (AVI file using Motion JPEG encoding)
                                                        % 'Uncompressed AVI' (Uncompressed AVI file with RGB24 video)
                                                    % For .mj2 files:
                                                        % 'Motion JPEG 2000' (Motion JPEG 2000 file)
                                                        % 'Archival' (Motion JPEG 2000 file with lossless compression)
                                                    % For .mp4 or .m4v files:
                                                        % 'MPEG-4' (MPEG-4 file with H.264 encoding)
                                                        
map = imread('../input/maps/irec2018/irec2018map.jpg');           % Load map of site made in google earth; square dimensions = 10000 ft (can 
                                        %   modify as needed). Recommend you run sims first to determine downrange,
                                        %   then make map in google earth larger than downrange values.
[Height,Width,RGB] = size(map);         % Dimensions of image in pixels
if Height > Width                       % Crop map to make image dimensions exactly equal (pixel height = pixel width)
    map = map(1:Width,:,:);
elseif Height < Width
    map = map(:,1:Height,:);
else
end

mapL = size(map,1);                     % Map side dimension in pixels
mapScale = 10000/mapL;                  % Scale factor of map (ft/pixel)
mapC = round(mapL/2);                   % Index of map center point

vid_framerate = 60;                     % Video framerate in frames per second
vid_quality = 100;                      % Video quality for JPEG AVI or MPEG-4
initial_azimuth = 30;                   % Initial z-axis rotation of 3D plot
initial_elevation = 45;                 % Initial elevation view of 3D plot (angle at which you "look down" on XY plane)

%% Creation and Sorting of Trajectories 
%---------------------------------------------------------------------------
a = round(max(abs(out.iPos(end,:,1))));         % Maximum x-range magnitude
sa = sign(max(out.iPos(end,:,1)));              % Sign of max x-range value ( (+)=East, (-)=West )
b = round(max(abs(out.iPos(end,:,2))));         % Maximum y-range value (North)
sb = sign(max(out.iPos(end,:,2)));              % Sign of max y-range value ( (+)=North, (-)=South )
[p,q] = max(out.tPos(:,:,3));           % Collect apogee values and indeces (respectively) of each MC into p & q vectors
c = max(p);                             % Absolute max apogee of all MC
iApg = max(q);                          % Index of absolute max apogee of all MC runs
vec = out.tPos(1:iApg,:,:);             % Cut off length of MC runs at index at index of abs. max apogee of all MC
x = vec(:,:,1);                         % X-position of rocket for MC runs 
y = vec(:,:,2);                         % Y-position of rocket for MC runs
z = vec(:,:,3);                         % Z-position of rocket for MC runs
L = size(vec,1);                        % # of iterations in vec
M = size(vec,2);                        % # of MC runs

%Impact Map vector data:
impvec = out.iPos(:,:,:);
impx = impvec(:,:,1);
impy = impvec(:,:,2);
impz = impvec(:,:,3);
impL = size(impvec,1);

% Extract ascent data points from x,y,z every 50 iterations (0.5sec): 
concatSize = 10;
X(1,:) = x(1,:);                                                            
Y(1,:) = y(1,:);
Z(1,:) = z(1,:);
for j = 2 : (ceil(L/concatSize)-1)
    X(j,:) = x(concatSize*j,:);
    Y(j,:) = y(concatSize*j,:);
    Z(j,:) = z(concatSize*j,:);
end

% Extract descent phase data points from impx, impy, impz every 50
% iterations (0.5sec): 
impX(1,:) = impx(1,:);                                                            
impY(1,:) = impy(1,:);
impZ(1,:) = impz(1,:);
for impj = 2 : (round(impL/concatSize)-1)
    impX(impj,:) = impx(concatSize*impj,:);
    impY(impj,:) = impy(concatSize*impj,:);
    impZ(impj,:) = impz(concatSize*impj,:);
end

% Sort the MC flights by APOGEE (for trajectory color assignment later); as
% well as cut off ascent vectors at their respective apogees, store these
% variable length vectors in cells. 
[B,index] = sort(Z(end,:));
q = round(q./concatSize);    % Lengths of all ascent vectors
if max(q) > size(Z,1) 
    [Qmax,QmaxIndex] = max(q)
    q(QmaxIndex) = Qmax-1
end
for j = 1 : M
    Xf{j} = X(1:q(index(j)),index(j)); 
    Yf{j} = Y(1:q(index(j)),index(j));  
    Zf{j} = Z(1:q(index(j)),index(j)); 
end

% Sort descent vectors in same order (also for color sorting):
for impj = 1 : M
    impXf(:,impj) = impX(:,index(impj));
    impYf(:,impj) = impY(:,index(impj));
    impZf(:,impj) = impZ(:,index(impj));
end

% Concatinate ascent vectors and descent vectors:
for j = 1:M
    Xftot{j} = [Xf{j};impXf(:,j)];
    Yftot{j} = [Yf{j};impYf(:,j)];
    Zftot{j} = [Zf{j};impZf(:,j)];
    Zlengths(j) = size(Zf{j},1)+size(impZf(:,j),1);
end
minLength = min(Zlengths); 

% Trim all vectors to same length (minLength in the line above), compile    
% all flights into single matrix instead of a cell:
for j = 1:M
    Xtemp = Xftot{j};
    Ytemp = Yftot{j};
    Ztemp = Zftot{j};
    XfFinal(:,j) = Xtemp(1:minLength);
    YfFinal(:,j) = Ytemp(1:minLength);
    ZfFinal(:,j) = Ztemp(1:minLength);
end

% Trim off ends of descent phase where Zpos=0 to the time at 
%  which the last trajectory hits the ground;
for j=1:M
    groundIndex = find(~ZfFinal(:,j));
    groundIndex = groundIndex(1);
    groundIndeces(j) = groundIndex;
end
[groundIndexMax,~] = max(groundIndeces);
for j = 1:M
    Xtraj(:,j) = XfFinal(1:groundIndexMax,j);
    Ytraj(:,j) = YfFinal(1:groundIndexMax,j);
    Ztraj(:,j) = ZfFinal(1:groundIndexMax,j);
end


%% Plotting of Trajectories/ Frame Creation/ Video File Creation
%---------------------------------------------------------------------------
h = figure('units','normalized','outerposition',[0 0 1 1]);           % Establish full screen figure
hold on;                                % Keep all flights on same plot
grid on;                                % Establish grid on plot
axis equal                              % Data units (1 ft) are of equal length regardless of axis total length

% Set axis limits to capture all MC's, set view angle (azimuth, elevation) to the appropriate x-y quadrant, 
%   and set axis labels to correspond with compass rose directions:

if a == 0 
    sa = 0;                          % Sometimes it sets sa | sb = -1 for the case a | b = 0, respectively. 
end                                  %   This fixes an error that may pop up because of that.
if b == 0
    sb = 0;
end

% if sa >= 0 && sb >= 0
%     if sa == 0
%         set(gca,'XLim',[-b/10 b/10], 'YLim', [-b/4 b], 'ZLim', [0 c]);
%     elseif sb == 0 
%         set(gca,'XLim',[-a/4 a], 'YLim', [-a/10 a/10], 'ZLim', [0 c]);
%     else 
%         set(gca,'XLim',[-a/4 a], 'YLim', [-b/4 b], 'ZLim', [0 c]);
%     end
%     map = map( round(mapC-b/4/mapScale):round(mapC+b/mapScale),...
%                round(mapC-a/4/mapScale):round(mapC+a/mapScale),...
%                : ); % Crop map to a & b dimensions
%     map = imresize(map, mapScale);
%     view(45,30); % Quadrant I
%     xlabel('East (ft)')
%     ylabel('North (ft)')
% elseif sa < 0 && sb > 0
%     set(gca,'XLim',[-a a/4], 'YLim', [-b/4 b], 'ZLim', [0 c]);
%     map = map( round(mapC-b/4/mapScale):round(mapC+b/mapScale),...
%                round(mapC-a/mapScale):round(mapC+a/4/mapScale),...
%                : ); % Crop map to a & b dimensions
%     map = imresize(map, mapScale);
%     view(45,30); % Quadrant II
%     xlabel('West (ft)')
%     ylabel('North (ft)')
% elseif sa < 0 && sb < 0
%     set(gca,'XLim',[-a a/4], 'YLim', [-b b/4], 'ZLim', [0 c]);
%     map = map( round(mapC-b/mapScale):round(mapC+b/4/mapScale),...
%                round(mapC-a/mapScale):round(mapC+a/4/mapScale),...
%                : ); % Crop map to a & b dimensions
%     map = imresize(map, mapScale);
%     view(45,30); % Quadrant III
%     xlabel('West (ft)')
%     ylabel('South (ft)')
% elseif sa > 0 && sb < 0
%     set(gca,'XLim',[-a/4 a], 'YLim', [-b b/4], 'ZLim', [0 c]);
%     map = map( round(mapC-b/mapScale):round(mapC+b/4/mapScale),...
%                round(mapC-a/4/mapScale):round(mapC+a/mapScale),...
%                :); % Crop map to a & b dimensions
%     map = imresize(map, mapScale);
%     view(45,30); % Quadrant IV
%     xlabel('East (ft)')
%     ylabel('South (ft)')
% end

% Full Map
set(gca,'XLim',[-a/2 1.25*a], 'YLim', [-b b], 'ZLim', [0 c]);
map = map( round(mapC-b/mapScale):round(mapC+b/mapScale),...
           round(mapC-a/2/mapScale):round(mapC+a/mapScale),...
           : ); % Crop map to a & b dimensions
map = imresize(map, mapScale);
view(initial_elevation, initial_azimuth); % Quadrant II
xlabel('East [ft]','FontSize',18)
ylabel('North [ft]','FontSize',18)
title('Trajectory Spread','FontSize',24)
zlabel('Altitude [ft]','FontSize',18)

%colorMap = colormap(flipud(winter(M)));                        % Sets parula-scheme color spectrum for flights, with M colors
colorMap = colormap(winter(M));

% Video creation:
vidPath = ['./videos/' vidName vidType];            % Define file path using video name 
traj = VideoWriter(vidPath, vidProfile);            % Assign file path to video
traj.FrameRate = vid_framerate;                                % Assign desired framerate 
                                                    %    (FR = 10 corresponds to trajectories playing in real time)

profileType1 = strcmp(vidProfile, 'Motion JPEG AVI'); % Check for Motion JPEG AVI or MPEG-4 video profile, 
profileType2 = strcmp(vidProfile, 'MPEG-4');          %     set video quality as necessary.
if profileType1 == 1 || profileType2 == 1
    traj.Quality = vid_quality;                             % Assign video quality, scale: 1(poor)-100(excellent)
else
end

% Option for plotting trajectory spreads as a static plot, and not a video.  (Comment out EVERYTHING after
% this 'for' loop if you want to do that, and just use the following 5 lines):
    % FRAMENR = size(Ztraj,1);
    % image('Xdata',[-a/2 1.25*a],'Ydata',[-b b],'Cdata',map)
    % for i = 1:M
    %     f(i) = plot3( Xtraj(1:FRAMENR,i), Ytraj(1:FRAMENR,i), Ztraj(1:FRAMENR,i), 'color', colorMap(i,:), 'LineWidth', 0.1);
    % end

open(traj)                                          % Open video file for writing
axis image                                          % Automatically scales cropped map to x-y plane

for frameNr = 1 : size(Ztraj,1)            % Loop through sim iterations (timesteps)
    cla                                 % Clear figure between each loop
    
%     if sa >= 0 && sb >= 0               % Plot map in proper x-y quadrant
%         image('Xdata',[-a/4 a],'Ydata',[-b/4 b],'Cdata',map)
%     elseif sa < 0 && sb > 0
%         image('Xdata',[-a a/4],'Ydata',[-b/4 b],'Cdata',map)
%     elseif sa < 0 && sb < 0
%         image('Xdata',[-a a/4],'Ydata',[-b b/4],'Cdata',map)
%     elseif sa > 0 && sb < 0                 
%         image('Xdata',[-a/4 a],'Ydata',[-b b/4],'Cdata',map)  
%     end
    image('Xdata',[-a/2 1.25*a],'Ydata',[-b b],'Cdata',map)
    for i = 1 : M                       % Loop through and plot each MC trajectory
        % Plots projection shadow points on down/cross-range grid, 
        %   colors match each associated trajectory:  
        q(i) = plot3( Xtraj(frameNr,i), Ytraj(frameNr,i), 0, '.', 'color', colorMap(i,:));
        % Plot comet trajectory lines, color of each trajectory determined
        %   by apogee:
        f(i) = plot3( Xtraj(1:frameNr,i), Ytraj(1:frameNr,i), Ztraj(1:frameNr,i), 'color', colorMap(i,:), 'LineWidth', 0.1);
    end
    frame = getframe(h);                % Stores figure in vector of frames
    writeVideo(traj, frame)             % Append frame to assigned video file
end

transitionFrames1 = 180;
for tframe1 = 1:transitionFrames1
    azimuth = 45 - (45/transitionFrames1)*tframe1;
    elevation = 30 + ((90-30)/transitionFrames1)*tframe1;
    view(azimuth, elevation);
    frame = getframe(h);
    writeVideo(traj,frame)
end

transitionFrames2 = 60;
for tframe2 = 1:transitionFrames2
    cla
    image('Xdata',[-a/2 1.25*a],'Ydata',[-b b],'Cdata',map)
    for i = 1:M
        f(i) = plot3( Xtraj(:,i), Ytraj(:,i), Ztraj(:,i), 'color', colorMap(i,:), 'LineWidth', 0.1);
        f(i).Color(4) = (1-(1/transitionFrames2)*tframe2);
        q(i) = plot3( Xtraj(end,i), Ytraj(end,i), 0, '.', 'color', colorMap(i,:),'MarkerSize',20);
        plot3(0,0,0,'*k','MarkerSize',20)
    end
    frame = getframe(h);
    writeVideo(traj,frame)
end
%% Impact Map
if isfield(out,'itime')
%     tImp = find 
% Find the last time to impact

    for n = 1:M %FIXME: Can probably remove this out of a for loop
        impind(n) = find(out.iPos(:,n,3) <= 0,1)-1;
    end
    iImp = max(impind);
    labelFont = 14;
    titleFont = 24;
    gridAlpha = 0.5;
    
    title('Trajectory Spread','FontSize',titleFont)
    
    ascent  = out; %FIXME: I'm nonsensical
    descent = out;
    sim     = inp.sim;

    %---Parametric Plot---%

%     figure
%     box  on
%     grid on
%     hold on

    for i = 1:sim.numMC

        xPos = [ascent.tPos(1:iApg,i,1); descent.iPos(1:iImp,i,1)];
        yPos = [ascent.tPos(1:iApg,i,2); descent.iPos(1:iImp,i,2)];
        zPos = [ascent.tPos(1:iApg,i,3); descent.iPos(1:iImp,i,3)];

        xImp(i) = xPos(impind(i));
        yImp(i) = yPos(impind(i));

%         H = plot(xPos,yPos,zPos);
%         H.LineWidth = 1;
%         H.Color = 'k';

    end

%     hold off

%     ind      = strfind(fileName,'_IMPACT');
%     titleStr = fileName(1:ind-1);
%     titleStr(titleStr == '_') = '|';

%     title(titleStr,'FontSize',titleFont)
%     xlabel('Downrange X [ft]')
%     ylabel('Downrange Y [ft]')
%     zlabel('Altitude [ft AGL]')

%     ax = gca;
% 
%     ax.FontSize   = labelFont;
%     ax.XMinorTick = 'on';
%     ax.YMinorTick = 'on';
%     ax.XMinorGrid = 'on';
%     ax.YMinorGrid = 'on';
%     ax.GridAlpha  = gridAlpha;
% 
%     domLim = round(1.5 * max(abs([xImp yImp])),-2);

%     view([45 15])
%     axis equal
%     xlim([-domLim domLim])
%     ylim([-domLim domLim])
%     zlim([0 inf])

    %---Impact Map---%

    xAvg   = mean(xImp);
    yAvg   = mean(yImp);
    rImp   = sqrt((xImp - xAvg).^2 + (yImp - yAvg).^2);
    rImp   = sort(rImp);
    cepLvl = flip([25 50 75 90 99]);
    ind    = ceil(cepLvl/100 * length(rImp));
    cep    = rImp(ind);

    cepAlpha = linspace(0.5,1,length(cep));
    cepClr   = parula(length(cep));

    for i = 1:length(cep)
    %FIXME: problem area
        cepX{i}  = xAvg-cep(i) : 0.01 : xAvg+cep(i);
        cepY1{i} = sqrt(cep(i)^2 - (cepX{i}-xAvg).^2) + yAvg;
        cepY2{i} = -cepY1{i} + 2*yAvg; 

    end

%     figure
%     box on
%     grid on
%     hold on

        for i = 1:length(cep)
            p = patch([cepX{i} flip(cepX{i})],[cepY1{i} cepY2{i}], cepClr(i,:));
            p.FaceAlpha = cepAlpha(i);
            P{i} = p;
        end

        for i = 1:length(cep) %FIXME cepY1, cepY2 are complex
           H = plot([cepX{i} flip(cepX{i})],[cepY1{i} cepY2{i}]);
           H.Color = 'k';
           H.LineWidth = 1.5;
        end

        %s = scatter(xImp,yImp,'Filled');
%         t = scatter(0,0,'Filled');

%     hold off

    s.MarkerFaceColor = 'r';
    s.MarkerEdgeColor = 'k';
    s.SizeData        = 10;
    s.LineWidth       = 1;

    t.MarkerFaceColor = 'k';
    t.SizeData        = 100;

%     title(titleStr,'FontSize',titleFont)
%     xlabel('Downrange X [ft]')
%     ylabel('Downrange Y [ft]')
% 
    l = legend( [P{1},P{2},P{3},P{4},P{5}], strsplit(num2str(cepLvl)), 'Location', 'northwest', 'FontSize',24);
    title(l,'CEP %')
% 
%     ax = gca;
% 
%     ax.FontSize   = labelFont;
%     ax.XMinorTick = 'on';
%     ax.YMinorTick = 'on';
%     ax.XMinorGrid = 'on';
%     ax.YMinorGrid = 'on';
%     ax.GridAlpha  = gridAlpha;
% 
%     domLim = 1.5 * max(abs([xImp yImp]));
% 
%     axis equal
%     xlim([-domLim domLim])
%     ylim([-domLim domLim])
    frame = getframe(h);
    writeVideo(traj,frame)
    frame = getframe(h);
    writeVideo(traj,frame)
    frame = getframe(h);
    writeVideo(traj,frame)
end

hold off
fprintf('\nVideo properties are as follows:\n\n')
disp(traj)
close(traj)
toc


