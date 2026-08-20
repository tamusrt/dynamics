%% RASAero II input file creation
% Using 30 files from Luke Adam's "brute force" .py script
% dev: 04/10/2021 Sarah Kinney
% mod: 04/10/2021 Nacho Durante

function finished = InputFileCreator(fname, folder)

    close all
    
    %% Constants
    filename = fname;
    alpha = 30;
    
    %% Create File
    headers = ['alpha	','mach	','xCP	','clCoast	','cdCoast	','clBoost	','cdBoost	'];
    fid = fopen(filename,'w');
    fprintf(fid, '%s%s%s%s%s%s%s', headers);
    fprintf(fid, '\n');
    fid = fclose(fid);
    
    %% Run through alpha data sets
    for i = 0:alpha
        fname = sprintf(folder + "/alpha%d.txt", i);
        InputFileAppend(filename, fname);
    end
    finished = true;
end
