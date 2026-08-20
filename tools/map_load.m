function [map_struct] = map_load(map_name)
%MAP_LOAD Load map from fs maps dir, including metadata
%   Detailed explanation goes here
    mapdir = srt_fs_path('input/maps');
    
    eval_file = fullfile(mapdir, map_name, 'map_info.m');
    if ~exist(eval_file, 'file')
        ME = MException('fs:mapNotFound', ...
            'Map "%s" not found!', map_name);
        throw(ME)
    end
    run(eval_file); % :(
    map_struct = map; % should be defined in the eval call
    
    % fully resolve map.img
    map_struct.img = fullfile(mapdir, map_name, map_struct.img);

    if ~exist(map_struct.img, 'file')
        ME = MException('fs:mapNotFound', ...
            'Map "%s" loaded, but map image not found!\n\tChecked for "%s"', map_name, map_struct.img);
        throw(ME)
    end
end

