%{
Texas A&M University Sounding Rocketry Team
SRT-5 | 2017-2018

%-------------------% 
      TAMU SRT
 ________  ________ ________   
|\   __  \|\  _____\\   __  \  
\ \  \|\  \ \  \__/\ \  \|\  \ 
 \ \   __  \ \   __\\ \   ____\
  \ \  \ \  \ \  \_| \ \  \___|
   \ \__\ \__\ \__\   \ \__\   
    \|__|\|__|\|__|    \|__|   

%-------------------% 

input_path:
    /tools/afp/run_sim.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
    (L) Jimenez, Emilio             (20190212)
     
Description:
    Runs one simulation with newest weather and oxidizer conditions. Does
    this by obtaining newest weather data log, estimating the amount of
    oxidizer needed to get to the correct apogee, rewritting the input file
    to contain newest values, running simulation, and outputting results on
    command window and on a dat file

Input(s):
    input_path          Address to srt_fs_auto.m input file
    filleID             Address to stf_fs_auto.out output file
    liveWeather         Address of the newest live data log
    targetApg           Target apogee in feet
    liveSimMc           Number of Monte Carlos used in live simulations
    sim                 Struct containing simulation parameters and toggles
    
Output(s):
    fs_auto_out.dat     Data file
%}

function [sim_path, live_data] = run_sim(input_path, output_path, log_path, liveWeather, railElev, railHead, targetApg, liveSimMC, sim, app, afp_data, Ox_Override, fillox)
    tstart = tic;
    %% Load Weather Data
    [weather, live_data] = get_weather(liveWeather, afp_data);
    barMsg = [];
    
    %% Creating new input file with weather data
    fprintf("Generating new input file...\n");
    input_gen(input_path, weather, liveSimMC, railElev, railHead, Ox_Override, fillox);
    
    nBack  = length(barMsg);
    fprintf(repmat('\b',1,nBack))  
    
    %% Run sims with new input file
    fprintf("Simulating flight...\n");
    sim_path = srt_fs_main(input_path);
    sim_out = load(sim_path);
           
    %% Output Data 
    inp = sim_out.inp;
    out = sim_out.out;
    apgMean = mean(max(out.tPos(:,:,3)));
    
    if (abs(apgMean - targetApg) > sim.tolApg) && (sim.troubleShoot == 0) && (Ox_Override == 0)
        % Altitude does not reach the target
        % Recalculating nitrous
        fprintf("Calculating Nitrous fill...\n");
        fillOx = find_nitrous(input_path,targetApg);
        nBack  = length(barMsg);
        fprintf(repmat('\b',1,nBack));
        
        % Runs new sim
        sim_path = srt_fs_main(input_path);
        sim_out = load(sim_path);
        inp = sim_out.inp;
        out = sim_out.out;
        apgMean = mean(max(out.tPos(:,:,3)));
        
    elseif (Ox_Override == 1)
        fillOx = fillox;
    elseif (sim.troubleShoot == 1)
        % Program is in troubleshooting mode 
        % Allocate a constant oxidizer value
        fillOx = 1.421788e+01;
    else
        fillOx = inp.prp.fillOx;
    end
    
    numMC = inp.sim.numMC;
    
    % Apogee
    summ.apgMean = max(out.tPos(:,:,3));
    
    % Recommended NO2
    summ.fillOx = fillOx;
    app.FillWeightlbmEditField.Value = fillOx;
       
    % Rail Exit Velocity & Min Stability
    for i = 1:numMC
        posMag = sqrt(sum(out.tPos.^2,3));
        velMag = sqrt(sum(out.tVel.^2,3));
        ind    = find(posMag(:,i) > ...
                 (inp.bod.railLength + (inp.bod.length - inp.bod.posCG)),1,'first');

        summ.railExit(i) = velMag(ind,i);
        summ.minStab(i) = out.stab(ind,i);
    end
    
    % Downrange North & East
    for i = 1:numMC
        l = length(out.iPos) - 1;
        summ.posEast(i) = out.iPos(l,i,1);
        summ.posNorth(i) = out.iPos(l,i,2);
        summ.posMag(i) = norm([out.iPos(1,i,1),out.iPos(1,i,2)]); 
    end
    
            
    summLabel = {'Apogee',...
                 'Recommended Fill',...
                 'Rail Exit Velocity',...
                 'Minimum Stability',...
                 'Downrange North',...
                 'Downrange South',...
                 'Downrange Magnitude',...
                 'Run Time'};
    
    summUnit  = {'ft',...
                 'lb',...
                 'ft/s',...
                 'cal',...
                 'ft',...
                 'ft',...
                 'ft',...
                 's'};

    summ.ttotal = toc(tstart);
    
    fields = fieldnames(summ);
    fprintf('\n');
       
    for i = 1:length(fields)
        
        stMean = mean(summ.(fields{i}));
        stMin = min(summ.(fields{i}));
        stMax = max(summ.(fields{i}));
        
        % Print to user
        fprintf(log_path,'%s \n',summLabel{i});
        fprintf(log_path,'\tMean \t%.2f \t[%s]\n',stMean,summUnit{i});
        fprintf(output_path,'%.2f\t',stMean);
        
        fprintf(log_path,'\tMin \t%.2f \t[%s]\n',stMin,summUnit{i});
        fprintf(output_path,'%.2f\t',stMin);
        
        fprintf(log_path,'\tMax \t%.2f \t[%s]\n\n',stMax,summUnit{i});
        fprintf(output_path,'%.2f\t',stMax);
        
        if i == length(fields)
           fprintf(log_path, '-----------------------\n');
           fprintf(output_path, '\n'); 
        end
    end
end