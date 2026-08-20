%cd ..;
clc; clear; close all;
set(0,'DefaultFigureWindowStyle','docked')
files = dir('./lzo_data/*.mat');
fileName = [{files.name}];
[~, idx] = sort([files.datenum]);
files = files(idx);
for i = 1:numel(files)
    
    data = load(['./lzo_data/' files(i).name]);
    out = data.out;
    [summ.apogee, iApg] = max(out.tPos(:,:,3));
    apg(i) = round(mean(summ.apogee));
    for n = 1:data.inp.sim.numMC
        impind(n) = find(out.iPos(:,n,3) <= 0,1)-1;
        xImp(n) = out.iPos(impind(n),n,1);
        yImp(n) = out.iPos(impind(n),n,2);
    end
    xAvg(i) = mean(xImp);
    yAvg(i) = mean(yImp);
    for n = 1:data.inp.sim.numMC %get flight paths
        m = [75, 200];
        xPos{i} = double([downsample(out.tPos(1:iApg,n,1),m(1)); downsample(out.iPos(1:impind(n),n,1),m(2))]);
        yPos{i} = double([downsample(out.tPos(1:iApg,n,2),m(1)); downsample(out.iPos(1:impind(n),n,2),m(2))]);
    end
    
end


cd ..;
%%
xGoal = -400;    % recovery position east [ft]
yGoal = 1900;    % recovery position north [ft]
filename = 'lzogif';

tHold = 1.5; % time to hold on last frame of each flight.
tHoldEnd = 4; %time to hold on last frame
frameRate = 30;
for i = 1:numel(files)
% Impact points
f = figure;
f.Name = 'recoverySpread1';
% img   = './input/maps/hutto_big.jpg';
img = './input/maps/irec2018map2.jpg';
px2ft = 4000/1263; %hutto_big.jpg
railX = 1900*px2ft;
railY = 1350*px2ft;
img   = imread(img);
    
ax = gca;
imSz  = size(img);
ax.XLim = [0 , imSz(2)*px2ft]  + [-railX -railX];
ax.YLim = [-imSz(1)*px2ft , 0] + [railY railY];
imagesc(ax.XLim, ax.YLim, flip(img,1));
set(gca,'ydir','normal');
box on
grid on
hold on
ax.XAxis.Exponent = 0;
if i >1
    w = plot(xAvg(1:i-1),yAvg(1:i-1),'Marker','*','MarkerFaceColor','b','MarkerEdgeColor','b'...
    ,'LineStyle','none','MarkerSize',7,'LineWidth', 0.5); 
    for k = 1:i-1
        historyLine{k} = plot(xPos{k},yPos{k});
        historyLine{k}.LineWidth = 0.8;
        historyLine{k}.Color = 'b';
    end
end
g = plot(xGoal,yGoal,'Marker','x','MarkerFaceColor','g','MarkerEdgeColor','g'...
    ,'LineStyle','none','MarkerSize',22,'LineWidth', 0.5); 
plot(0,0,'Marker','o','MarkerFaceColor','k','MarkerEdgeColor','k','MarkerSize', 10);
titleStr = ['Iteration: ' num2str(i)];
title(titleStr)
xlabel('Downrange E [ft]')
ylabel('Downrange N [ft]');
srt_fs_format('powerpoint');
if i >1
    for k = 1:numel(historyLine)
        historyLine{k}.LineWidth = 0.9;
    end
end
for frameNr = 1:numel(xPos{i})      % Loop through iterations
    %cla                                 % Clear figure between each loop                     % Loop through and plot each MC trajectory
        % Plots projection shadow points on down/cross-range grid, 
        %   colors match each associated trajectory:  
        % Plot comet trajectory lines, color of each trajectory determined
        %   by apogee:
    %f(i) = plot3( Xf(1:frameNr,i), Yf(1:frameNr,i), Zf(1:frameNr,i), 'color', mymap(i,:), 'LineWidth', 0.1);
    h = plot(xPos{i}(1:frameNr),yPos{i}(1:frameNr),'LineWidth', 2.6, 'Color', 'r');
    frames{i}(frameNr) = getframe(f);     % Stores figure in vector of frames
end

s = plot(xAvg(i),yAvg(i),'Marker','o','MarkerFaceColor','r','MarkerEdgeColor','k'...
    ,'LineStyle','none','MarkerSize',9,'LineWidth', 1.5); 
% Launch site

uistack(s,'top');
frameFinal = getframe(f);
for frameNr = 1:round(frameRate*tHold)
    frames{i}(end+1) = frameFinal;
end

hold off
% titleStr = ['Iteration: ' num2str(i) ' | Apogee: ' num2str(apg(i)) '[ft]'];

% drawnow 
% frame{i} = getframe(f); 

% im{i} = frame2im(frame); 
% [imind,cm] = rgb2ind(im,256); 
% % Write to the GIF File 
% if i == 1 
%   imwrite(imind,cm,filename,'gif', 'Loopcount',inf); 
% else 
%   imwrite(imind,cm,filename,'gif','WriteMode','append'); 
% end 

end
framesAll = [];
for i = 1:numel(files)
    framesAll = [framesAll, frames{i}];
end
vidPath = './tools/lzo_videos/lzo_post.avi';             % Define file path using video name 
traj = VideoWriter(vidPath);                        % Assign file path to video
traj.FrameRate = frameRate;                                % Assign desired framerate (FR = 10 corresponds to trajectories playing in real time)
traj.Quality = 100;                                 % Assign video quality, scale: 1(poor)-100(excellent)
open(traj)
writeVideo(traj, framesAll)                           % Write video to assigned file name using 'frames1' vector
close(traj)
% create the video writer with 1 fps
%  writerObj = VideoWriter('lzo_post','MPEG-4');
%  writerObj.FrameRate = 1;
%  % set the seconds per image
%  secsPerImage = ones(1,numel(files)).*1;
%  % open the video writer
%  open(writerObj);
%  % write the frames to the video
%  for i=1:length(frame)
%      % convert the image to a frame
%      for v=1:secsPerImage(i) 
%          writeVideo(writerObj, frame{i});
%      end
%  end
%  % close the writer object
%  close(writerObj);



