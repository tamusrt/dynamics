

function [ ] = srt_sensitivity( varargin )
    close all
    clc
    addpath('../..')
    addpath('./input')
    
    %-Load input file
%     if nargin == 0
%         [fileName,fileDir,~] = uigetfile('./sensitivity_study/input/*.inp*','Select Input File');
%         filePath = [fileDir fileName];
%     else
%         filePath = varargin{1};
%     end

    inpPath = 'study_input_hem.inp'; %<- Development Tool. Delete when done    
    study_inp = srt_sensitivity_load(inpPath);
    if (study_inp.hem_study)
        study_inp.fill_calc = false;
    end
    
    %-Create study output variable
    % Measure total number of combinations
    input_labels = fieldnames(study_inp);
    n = 1;
    for i = 1:numel(input_labels)
        if (input_labels(i) ~= "base_input")&&(input_labels(i) ~= "fill_calc")&&(input_labels(i) ~= "hem_study")
            n = n * ( study_inp.(input_labels{i}).n + 1);
        end
    end
    if study_inp.fill_calc
        n = n*2;
    end
    
    % Measure total number of outputs
    if (~study_inp.hem_study)
        output_list = {"aero_file"; ...
                       "apogee"; ...
                       "cd_0"; ...
                       "downrange"; ...
                       "rail_ex_vel"; ...
                       "acc_max"; ...
                       "mach_max"; ...
                       "fin_rat"; ...
                       "tank_length"; ...
                       "fin_b"; ...
                       "fin_cr"; ...
                       "fin_ct"};
    else
        output_list = {"peak_thrust"; ...
                   "avg_thrust"; ...
                   "impulse"; ...
                   "regression_rate"; ...
                   "exit_vel"; ...
                   "exit_mach"; ...
                   "exit_pres"; ...
                   "isp"};
    end
    input_list = input_labels(1:end-3);
                   
    mi = numel(input_list);
    mo = numel(output_list);
    study_in = zeros(n,mi);
    study_out = zeros(n,mo);
    
    % Creates initial input values from input file
    input_vals = cell(numel(input_labels)-2,1);
    
    for i = 1:numel(input_labels)
        if (input_labels{i} == "fill_calc")&&(study_inp.fill_calc == 1)
            temp = [1 0];
            input_vals{i,1} = temp;
        elseif (input_labels{i} == "fill_calc")&&(study_inp.fill_calc == 0)
            temp = [0];
            input_vals{i,1} = temp;
        elseif (input_labels(i) ~= "base_input") && (input_labels(i) ~= "hem_study")
            temp = zeros(1, study_inp.(input_labels{i}).n + 1);
            for j = 1:(study_inp.(input_labels{i}).n + 1)
                temp(j) = study_inp.(input_labels{i}).curr + (j - ( study_inp.(input_labels{i}).n/2 + 1))*study_inp.(input_labels{i}).delta;
            end
            input_vals{i,1} = temp;
        end
    end
    
    
    % Populate input matrix with initial values
    i = 1;
    for j = 1:numel(input_vals{1})
        for k = 1:numel(input_vals{2})
            for l = 1:numel(input_vals{3})
                for h = 1:numel(input_vals{4})
                    for g = 1:numel(input_vals{5})
                            study_in(i,1) = i;
                            study_in(i,2) = input_vals{1}(j);
                            study_in(i,3) = input_vals{2}(k);
                            study_in(i,4) = input_vals{3}(l);
                            study_in(i,5) = input_vals{4}(h);
                            study_in(i,6) = input_vals{5}(g);  
                            i = i + 1;
                    end
                end
            end
        end
    end
%     disp(study_in)
    
    % Start runs
    for i = 1:n
        if ~study_inp.hem_study
            %% FS

            % Create input file


            % Run Sim
            % Extract results

            % Open output study CSV
            % Print to study output CSV
            % Close output study CSV

            % Delete sim output file
        
        else
            %% HEM
        
            % Create input file
            inputCell = importdata(study_inp.base_input);
            inputCell{4} = sprintf('\taInj\t\t= %.4f', study_in(i,2));
            inputCell{10} = sprintf('\tvolTank\t\t= %.4f', study_in(i,3));
            inputCell{23} = sprintf('\tl\t\t\t= %.4f', study_in(i,4));
            inputCell{24} = sprintf('\tdGrain\t\t= %.4f', study_in(i,5));
            inputCell{25} = sprintf('\tdPort\t\t= %.4f', study_in(i,6));
            
            fid = fopen('./input/nova_i.dat', 'wt+');
            fprintf(fid, '%s\n', inputCell{:});
            fclose(fid);

            % Run Sim
            % Extract results

            % Open output study CSV
            % Print to study output CSV
            % Close output study CSV

            % Delete sim output file
        end
    end
    
    
    % Plot Results
    
end