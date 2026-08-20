function [fillOx] = srt_lzo_nitrous(goal, sim)

tFlight = 35;
numIter = 1;
numMC = 1;
error   = 50; 
fill(1) = 5;
fill(2)= 26;

apg(1)   = getApg(sim.filePath,fill(1),1,25); 
apg(2) = getApg(sim.filePath,fill(2),1,55);
apgL = apg(1);
apgU = apg(2);

% Upper and lower bounds
fillL = fill(1);
fillU = fill(2);
for i = 3:3+numIter
    fill(i) = ((apgU-goal.apg)*fillL-(apgL-goal.apg)*fillU)/(apgU-apgL);
    apg(i) = getApg(sim.filePath,fill(i),numMC,tFlight);
    if abs(apg(i)-goal.apg)<error % Was guess within error bound?
        break; % target fill found
    elseif sign(apg(i) - goal.apg) == 1 % Was guess above target?
        fillU = fill(i); % Guess ---> new upper
        apgU = apg(i);
    elseif sign(apg(i) - goal.apg) == -1 % Was guess below target?
        fillL = fill(i); % Guess ---> new lower
        apgL  = apg(i);
    end
end
fillOx = fill(end);

function [meanApg] = getApg(baseFile,fillOx,numMC,timeFlight) 
    fid = fopen(baseFile);
    baseCell      = textscan(fid,'%s','Delimiter','');
    inputCell     = baseCell{1};
    for j = 1:length(inputCell)
        if strfind(inputCell{j},'fillOx')
            txt = sprintf('\tfillOx = %d;',fillOx);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'numMC')
            txt = sprintf('\tnumMC = %i;',numMC);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'timeFlight')
            txt = sprintf('\ttimeFlight = %d;',timeFlight);
            inputCell{j} = txt;
        elseif strfind(inputCell{j}, 'runRecov')
            txt = sprintf('\trunRecov = %s;',"false");
            inputCell{j} = txt;
%             break;
        end
    end
    exportFile = baseFile;
    fid = fopen(exportFile,'w');
    fprintf(fid,'%s\n',inputCell{:});
    outputPath = srt_fs_main(baseFile);
    data = load(outputPath);
    [summ.apogee, ~] = max(data.out.tPos(:,:,3));
    meanApg = mean(summ.apogee)
end

end