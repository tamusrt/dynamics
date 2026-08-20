clc; clear all; close all

%% Import Data


load viz1kMC;

time = dat.time;            alpha = dat.alpha;
mach = dat.mach;            cp = 145-dat.xCP;
tPos = squeeze(dat.tPos);   aPos = squeeze(dat.aPos)*180/pi;

clear dat inp

%% Operational Constants

make_video = true;              % video creation
video_filename = 'sample_video'; % video filename
t_step = time(2) - time(1);      % time step of input data
int = floor(1/t_step/30);        % processing interval (3 is typical for t_step = 0.01)

%% Geometric Model Constants

l_tot   = 145;                  % total length              [in]
l_t     = 10.375;               % tailcone length           [in]
l_n     = 25.5;                 % nosecone length           [in]
l_b     = l_tot-l_t-l_n;        % body length               [in]
d_b     = 8.5;                  % body diameter             [in]
d_t     = 5.5;                  % tailcone diameter         [in]
n       = 60;                   % surface precision         [in]
f       = 40;                   % flame strength            [in]

%% Rocket Model Generation, Positioning, and Scaling

% Tailcone
    t = 0:0.1:d_b-d_t;
    [x_tail, y_tail, z_tail] = cylinder((d_t+t)/2, n);
    z_tail = l_t*z_tail;

% Body
    [x_body, y_body, z_body] = cylinder(d_b/2, n);
    z_body = l_b*z_body+l_t;

% Nosecone
    t = 0:0.25:d_b^2/4;
    [x_nose, y_nose, z_nose] = cylinder((t.^(1/2)), n);
    z_nose = -l_n*z_nose+l_n+l_b+l_t;

% Fins
    x_fin = [d_t/2 10.5 10.5 d_b/2];
    y_fin = [0 0 0 0];
    z_fin = l_t*[0 0.2 0.6 1];

% Flame
    t = 0:0.1:(d_t/2)^(5/4);
    [x_flame, y_flame, z_flame] = cylinder((d_t/2-t.^(4/5)), n);
    z_flame = -f*z_flame;

%% Figure Initialization and Composite Body Generation

figure('units','normalized','outerposition',[0 0 1 1]);

%% 3-DOF Rotation Subplot
    
    subplot(2, 2, [1 3]);
    lighting gouraud;

% Establish Bodies and Surfaces
    hold on;
    rocket(1) = surf(x_nose, y_nose, z_nose, 'FaceColor', [1 1 1], 'EdgeAlpha', 0.1);
    rocket(2) = surf(x_body, y_body, z_body, 'FaceColor', [1 1 1], 'EdgeAlpha', 0.1);
    rocket(3) = surf(x_tail, y_tail, z_tail, 'FaceColor', [1 1 1], 'EdgeAlpha', 0.1);
    rocket(4) = fill3(x_fin, y_fin, z_fin, [1 1 1]);
    rocket(5) = fill3(x_fin, y_fin, z_fin, [1 1 1]);
    rocket(6) = fill3(x_fin, y_fin, z_fin, [1 1 1]);
    rocket(7) = surf(x_flame, y_flame, z_flame, 'FaceColor', [0.8 0.4 0.2], 'EdgeAlpha', 0.0);
    hold off;

% Locate Radial Fins
    rotate(rocket(5), [0 0 1], 120, [0 0 1]);
    rotate(rocket(6), [0 0 1], 240, [0 0 1]);

% Prepare Subplot Environment
    axis equal;                          
    axis([-0.5*l_tot 0.5*l_tot -0.5*l_tot 0.5*l_tot -f l_tot]);
    axes = gca;                         axes.Projection = 'perspective';         
    axes.XTickLabel = {};               axes.YTickLabel = {};   
    axes.ZTickLabel = {};               axes.TickLength = [0 0];
    axes.Clipping = 'off';              view([55 15]);
    format_figure;

%% 3-DOF Translation Subplot

    subplot(2, 2, [2 4]);
    lighting gouraud;

% Establish Trajectory Comet
    hold on
    trajectory = plot3(tPos(1, 1), tPos(1, 2), tPos(1, 3), 'linewidth', 2, 'color', [0.2 0.6 0.95]);
    dot = scatter3(tPos(1, 1), tPos(1, 2), tPos(1, 3));
    hold off
    
% Prepare Subplot Environment
    [xl, ~] = min(tPos(:, 1));  [xu, ~] = max(tPos(:, 1));
    [yl, ~] = min(tPos(:, 2));  [yu, ~] = max(tPos(:, 2));
    [zl, ~] = min(tPos(:, 3));  [zu, ~] = max(tPos(:, 3));
    axis equal
    axis([-inf inf 1.1*yl 1.1*yu 0 1.1*zu]);
    axes = gca;                         axes.Projection = 'perspective';
    view([55 15]);
    ylabel('Downrange [ft]', 'FontWeight', 'bold');
    zlabel('Altitude (AGL) [ft]', 'FontWeight', 'bold');
    format_figure;

%% Flight Visualization

% Initialize Video
    if make_video == true
        video_object = VideoWriter(strcat(video_filename,'.avi'));
        open(video_object);
    end
    
% Calculate Critical Indices
    [~, i_bo] = max(mach);  t_bo = time(i_bo);  % burn index
    [~, i_ap] = max(tPos(:, 3));                % apogee index

for i = int+1:int:i_ap+int
    
    %% 3-DOF Rotation Subplot
    
    subplot(2, 2, [1 3])
    
    % Update Title String
        title_string = sprintf('Pitch | %1.1f deg\nAlpha | %1.1f deg', 90-aPos(i, 1), alpha(i));
        title(title_string, 'Color', 'white');
    
    % Update Flame Appearance
        if time(i) > t_bo
            rocket(7).Visible = 'off';
        else
            rocket(7).FaceColor = [0.95+rand/20, 0.5+rand/10, 0.1+rand/20];
            rocket(7).ZData = z_flame+randn/20*z_flame;
        end
    
    % Update Pitch and Yaw
        rotate(rocket, [1 0 0], aPos(i, 1)-aPos(i-int, 1), [0 0 cp(i)]);
        rotate(rocket, [0 1 0], aPos(i, 2)-aPos(i-int, 2), [0 0 cp(i)]);
    
    % Update Roll
        x = rocket(2).XData(2, 1)-rocket(2).XData(1, 1);
        y = rocket(2).YData(2, 1)-rocket(2).YData(1, 1);
        z = rocket(2).ZData(2, 1)-rocket(2).ZData(1, 1);
        rotate(rocket, [x y z], aPos(i, 3)-aPos(i-int, 3), [0 0 cp(i)]);
    
    %% 3-DOF Translation Subplot
   
    subplot(2, 2, [2 4]);
    
    % Update Title String
        title_string = sprintf('Time | T+%1.1f s\nAltitude | %1.0f ft\nSpeed | Mach %1.2f', ...
            time(i), tPos(i, 3), mach(i));
        title(title_string, 'Color', 'white');
    
    % Update Trajectory Comet
        delete(trajectory); delete(dot);

        hold on;
        if i < i_bo
            trajectory(1) = plot3(tPos(1:i, 1), tPos(1:i, 2), tPos(1:i, 3), ...
                'linewidth', 2, 'color', [1 0.5 0]);
        else
            trajectory(1) = plot3(tPos(1:i_bo, 1), tPos(1:i_bo, 2), tPos(1:i_bo, 3), ...
                'linewidth', 2, 'color', [1 0.5 0]);
            trajectory(2) = plot3(tPos(i_bo:i, 1), tPos(i_bo:i, 2), tPos(i_bo:i, 3), ...
                'linewidth', 2, 'color', [0.2 0.6 0.95]);
            trajectory(3) = text(double(tPos(i_bo, 1)), 300+double(tPos(i_bo, 2)), ...
                double(tPos(i_bo, 3)), 'Burnout', 'Color', [1 1 1], 'FontWeight', 'bold', ...
                'FontSize', 12);
            trajectory(4) = scatter3(tPos(i_bo, 1), tPos(i_bo, 2), tPos(i_bo, 3), 24, [1 1 1], ...
                'filled');
            if i >= i_ap
                trajectory(5) = text(double(tPos(i_ap, 1)), 300+double(tPos(i_ap, 2)), ...
                    double(tPos(i_ap, 3)), 'Apogee', 'Color', [1 1 1], 'FontWeight', 'bold', ...
                    'FontSize', 12);
                trajectory(6) = scatter3(tPos(i_ap, 1), tPos(i_ap, 2), tPos(i_ap, 3), 24, ...
                    [1 1 1], 'filled');
            end
        end
        dot = scatter3(tPos(i, 1), tPos(i, 2), tPos(i, 3), 36, [1 1 1], 'filled');
        drawnow;
        hold off;
    
    % Update Video Frame
        if make_video == true
            writeVideo(video_object, getframe(gcf));
        end

end

% Save and Close Video
    if make_video == true
        close(video_object);    
        fprintf(strcat('\nvideo written to /', video_object.Path, '\n'));
    end
    fprintf('visualization completed on %s\n', string(datetime('now')));
    
%% Function Definition

function [ ] = format_figure( )

lgd = legend;

if ~isempty(lgd)
    lgd.Color = 'black';
    lgd.TextColor = 'white';
end

box off
set(gcf, 'color', 'black')
set(gca, 'color', 'black')
set(gca, 'xcolor', 'white')
set(gca, 'ycolor', 'white')
set(gca, 'zcolor', 'white')

a = findobj(gcf);
alltext = findall(a, 'type', 'text');
set(alltext, 'color', 'white');

set(gcf, 'InvertHardcopy', 'off');

grid on

end