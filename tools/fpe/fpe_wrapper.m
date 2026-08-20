close all
clear
clc
set(0,'DefaultFigureWindowStyle','docked')

%% Initialize
tic
cd ..;
files = dir('./tools/fpe/rex/input/*.inp');
myPool = parpool(4);
tCase  = length(files);

parfor i = 1:tCase
    
    fileDir  = files(i).folder;
    fileName = [fileDir '/' files(i).name];
    
    try
        srt_fs_main(fileName);
    catch
        continue
    end
    
end

delete(myPool)

toc
