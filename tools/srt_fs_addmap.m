% use http://maps.google.com/mapfiles/kml/shapes/cross-hairs.png 
% 1. Using GEarth, place 3 markers around the launch site: top left, bottom
% left, bottom right
% 2. change each marker icon to crosshair
% 3. press n (north up). Press u (perpendicular view)
% 4. Use the File>Save feature to save at max resolution, zoom in but
% include the crosshairs
% 5. call srt_fs_addmap(<exported_image>), and follow directions
function [ ] = srt_fs_addmap(map_src)
%SRT_FS_ADDMAP Import map image with known marker locations
    fprintf("Select top left, bottom right points\n\tPress enter when done\n");
    map_img = imread(map_src);

    f = figure;
    imshow(map_img);
    r = images.roi.Polyline;
    draw(r);
    
    iCoords = r.Position; % image coords. Row1 = top left, row2 = bot left
%     [ptsX, ptsY] = getpts(f);
    close(f);
    coords.topLeft = getLatLon('top left');
    coords.botLeft = getLatLon('bot left');
    coords.botRight = getLatLon('bot right');
    % [lon lat] = [row col 1] * R
    % b = Ax
    LLMat = fliplr([struct2array(coords.topLeft); ...
                    struct2array(coords.botLeft); ...
                    struct2array(coords.botRight)]);
    RCMat = round([fliplr(iCoords) [1;1;1]]);
    R = RCMat\LLMat;
    disp(R);
    
    map.R = R;
    [~, filename, fileExt] = fileparts(map_src);
    map.img = ['./' filename fileExt];
    
    mapname = input('Enter map name ?> ', 's');
    mapdir = srt_fs_path(sprintf('input/maps/%s', mapname));
    fprintf('Writing map to %s\n', mapdir);
    if ~exist(mapdir, 'dir')
        mkdir(mapdir);
        matlab.io.saveVariablesToScript(fullfile(mapdir, 'map_info'), {'map'});
        
        copyfile(map_src, fullfile(mapdir, map.img));
    else
        error("Map already exists! R matrix printed above. Manually create map_info.m.");
    end
end
% geoshow(map.img, 

function coords = getLatLon(msg)
    fprintf('Enter coords for %s:\n', msg);
    coords.lat = input('Enter lat: ?> ');
    coords.long = input('Enter long: ?> ');
end
