%close all
clear
clc
set(0,'DefaultFigureWindowStyle','docked')

%% User Settings

%alpha refinement factor; interpolation level; if data given
%in 0.5 deg increments, data output in 0.5/refine increments
refine   = 4; 
machCut  = 1.5;
machStep = 0.01;
fileDir  = 'C:/Users/Luke Adams/Desktop/lzrs_export/';
fileOut  = srt_fs_path('input/aero/lzrs_i.dat');
dirCont  = dir([fileDir '*.txt']);
nFile    = length(dirCont);
CG       = 86;
bodOD    = 8.77;

%% Parse Data

%output 
mach    = cell(1,nFile);
alpha   = cell(1,nFile);
xCP     = cell(1,nFile);
clCoast = cell(1,nFile);
cdCoast = cell(1,nFile);
clBoost = cell(1,nFile);
cdBoost = cell(1,nFile);

for i = 1:nFile

    fileName = [fileDir dirCont(i).name];
    fid      = fopen(fileName);
    txtLine  = fgetl(fid);
    num      = 0;
    level    = 0;

    while ischar(txtLine)

        data = sscanf(txtLine,'%f');

        if ~isempty(data)

            level = level + 1;

            if level == 4

                mach{i}  = [mach{i} ; data(1)];
                alpha{i} = [alpha{i}; data(2)];
                xCP{i}   = [xCP{i}  ; data(4)];

            elseif level == 5

                clCoast{i} = [clCoast{i}; data(3)];
                cdCoast{i} = [cdCoast{i}; data(4)];

            elseif level == 6

                clBoost{i} = [clBoost{i}; data(3)];
                cdBoost{i} = [cdBoost{i}; data(4)];
                num     = num + 1;
                level   = 0;

            end
        end

        if num < (machCut/machStep)
            txtLine = fgetl(fid);
        else
            txtLine = [];
        end

    end

    fclose(fid);

end

%% Data Refinement

data = [];

data.alpha   = [alpha{:}];
data.mach    = [mach{:}];
data.xCP     = [xCP{:}];
data.clCoast = [clCoast{:}];
data.cdCoast = [cdCoast{:}];
data.clBoost = [clBoost{:}];
data.cdBoost = [cdBoost{:}];

fields = fieldnames(data);

alphaInt = unique(data.alpha);
alphaInt = alphaInt(1) : diff(alphaInt(1:2))/refine : alphaInt(end); 

for i = 1:length(fields)
    
    for j = 1:size(data.mach,1)
        
        ppInt = griddedInterpolant(data.alpha(j,:),data.(fields{i})(j,:),'pchip');
        
        out.(fields{i})(j,:) = ppInt(alphaInt);
    
    end
    
end

%% Plot Output

out.mach    = [mach{:}];
out.alpha   = [alpha{:}];
out.xCP     = [xCP{:}];
out.clCoast = [clCoast{:}];
out.cdCoast = [cdCoast{:}];
out.clBoost = [clBoost{:}];
out.cdBoost = [cdBoost{:}];

fields = fieldnames(out);
alphaLen = 1:6;
lnClr = jet(length(alphaLen));

for i = 3:length(fields)
    f = figure;
    ax = axes(f);
    hold(ax, 'on');
    grid(ax, 'on');
    box(ax, 'on');
    
    legStr = {};
   
    for j = 1:numel(alphaLen)
    
        h = plot(ax, out.mach(:,j),out.(fields{i})(:,j), ...
            'Color', lnClr(j,:), ...
            'LineWidth', 2 ...
        );
        
        legStr = [legStr sprintf('%s^o',num2str(out.alpha(1,j)))];
        
    end
    
    hold(ax, 'off');
    
    xlabel(ax, 'Mach No. [-]')
    ylabel(ax, fields{i})
    titleStr = sprintf('%s vs. Mach No.',fields{i});
    title(ax, titleStr,'FontSize',20)

    ax.FontSize = 16;
    ax.XMinorGrid = 'on';
    ax.XMinorTick = 'on';
    ax.YMinorGrid = 'on';
    ax.YMinorGrid = 'on';
    ax.GridAlpha  = 0.5;
    
    leg = legend(ax, legStr);
    leg.Title.String = 'AoA';
    leg.Location     = 'eastOutside';
    srt_fs_format('powerpoint');
    
end

%% File Output

fid = fopen(fileOut,'w+');
headTxt = sprintf('%s\t',fields{:});
fprintf(fid,'%s\n',headTxt(1:end-1));
    
for i = 1:size(out.alpha,2)
    
    for j = 1:machCut/machStep
        
        data = zeros(1,length(fields));
    
        for k = 1:length(fields)
    
            data(k) = out.(fields{k})(j,i);
    
        end
        
        fprintf(fid,'%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\n',data);
        
    end

end

fclose(fid);
