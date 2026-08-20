%% RASAero II input file creation
% Using 30 files from Luke Adam's "brute force" .py script
% Use in conjunction with Nacho Durante's "aero_post.py" script for proper
% formatting
% dev: 04/10/2021 Sarah Kinney
% mod: 11/30/2023 Nacho Durante

close all
clear
clc

%% Constants
filename = 'lzrs_i_14k.dat';
alpha = 30;

%% Create File
headers = ['alpha	','mach	','xCP	','clCoast	','cdCoast	','clBoost	','cdBoost	'];
fid = fopen(filename,'w');
fprintf(fid, '%s%s%s%s%s%s%s', headers);
fprintf(fid, '\n');
fid = fclose(fid);

%% Run through alpha data sets
for i = 0:alpha
    fname = sprintf('alpha%d.txt', i);
    InputFileAppend(filename, fname);
end
    