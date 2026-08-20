function [study] = srt_sensitivity_load(filePath)

%% Process Input File
 
%% Process Input File
 
fid      = fopen(filePath,'r');
txtLine  = fgetl(fid);
level    = 1;

% Reference variables
lineNum  = 1;
numBadRef = 0;
ref = struct([]);

while ischar(txtLine)
    skipLine = false; % Flag to skip assignment if variable contains reference
    
    % Comment
    if strfind(strtrim(txtLine),'//') == 1
        ...
            
    % Level down
    elseif any(txtLine == '{')
        level = level + 1;
        
    % Level up
    elseif any(txtLine == '}')
        level = level - 1;
        
    % Unknown characters
    elseif ~any(isletter(txtLine))
        ...
            
    % Class initialization
    elseif level == 1
        className = strtrim(txtLine);
        
    % Class --> variable assignment
    elseif level == 2
        
        txtLine(txtLine == ';') = [];
        txtLine = strsplit(txtLine,'//');
        txtLine = txtLine{1};
        txtLine = strsplit(txtLine,'=');
        txtLine = strtrim(txtLine);
        varName = txtLine{1};
        
        if length(txtLine) > 1
            value = txtLine{2};
        else
            value = [];
        end
            
        % Checks for string
        if any(value == '"')
            
            value(value == '"') = [];
        % Does variable contain reference
        elseif contains(string(value), 'ref(') 
            
            % Isolate variable being referenced
            [sInd, eInd] = regexp(value, 'ref\((?<=\()[^)]*(?=\))');
            refVar = value(sInd+4:eInd); 
            try 
                % Attempt to evaluate reference value
                refVal = eval(['data.(className).' refVar]);
                value = [value(1:sInd-1) '(' num2str(refVal) ')' value(eInd+2:end)];
                value = eval(value);
                
            catch
                % If reference variable doesn't exist yet store reference info for next iteration of evaluation
                numBadRef = numBadRef+1;
                skipLine = true; % Skip value assignment
                
                % Store reference info
                if isfield(ref,className) 
                    ref.(className).refTxt{end+1} = value;
                    ref.(className).refLine(end+1) = lineNum;
                    ref.(className).refVar{end+1} = refVar;
                    ref.(className).sInd(end+1) = sInd;
                    ref.(className).eInd(end+1) = eInd;
                    ref.(className).varName{end+1} = varName;
                else
                    % Used if reference is first in its class
                    ref(1).(className).refTxt{1} = value;
                    ref(1).(className).refLine(1) = lineNum;
                    ref(1).(className).refVar{1} = refVar;
                    ref(1).(className).sInd(1) = sInd;
                    ref(1).(className).eInd(1) = eInd;
                    ref(1).(className).varName{1} = varName;
                end
            end
            
        elseif length(value) >2 && value(1) == '[' && any(value == ']')
            value = strsplit(value,{'[',']'});
            modifier = value{3};
            value = value{2};
            value = strsplit(value,',');
            value = char(value);
            value = str2num(value)';
            if ~isempty(modifier)
                value = eval(['[', num2str(value), '].', modifier]);
            end
            
        % Evaluates any expressions with arithmetic operators
        elseif any(value == '+') || any(value == '-') || ...
               any(value == '*') || any(value == '/') || ...
               any(value == '^')
            
            value = eval(value);
            
        % Checks for boolean
        elseif strcmpi(value, 'true')
            
            value = true;
            
        elseif strcmpi(value, 'false')
            
            value = false;
        
        % Converts to double if no operators or alpha characters
        elseif ~isempty(value)
            value = str2double(value);
        end
        
        % Store classes and variables
        % Skip if assignment contained reference
        if ~skipLine
        
            data.(className).(varName) = value;
        end
    
    % Class --> variable --> property assignment
    elseif level == 3
        
        txtLine(txtLine == ';') = [];
        txtLine  = strsplit(txtLine,'//');
        txtLine  = txtLine{1};
        txtLine  = strsplit(txtLine,'=');
        txtLine  = strtrim(txtLine);
        propName = txtLine{1};
        value    = txtLine{2};
        
        % Checks for string
        if any(value == '"')
            
            value(value == '"') = [];
        
        % Evaluates any expressions with arithmetic operators
        elseif any(value == '+') || any(value == '-') || ...
               any(value == '*') || any(value == '/') || ...
               any(value == '^')
            
            value = eval(value);
            
        % Checks for boolean
        elseif strcmpi(value, 'true')
            
            value = true;
            
        elseif strcmpi(value, 'false')
            
            value = false;
        
        % Converts to double if no operators or alpha characters
        elseif ~isempty(value)
            value = str2double(value);
            
        end
        
    
        % Store variable properties

        del.(className).(varName).(propName) = value;

        
    end
    
    txtLine  = fgetl(fid);
    lineNum  = lineNum + 1;
    
end

% Evaluate stored references
while(numBadRef > 0)
    nResolved = 0;
    refClass = fieldnames(ref);
    for i = 1:numel(refClass)
        for j = 1:numel(ref.(refClass{i}).sInd)
            refTxt      = ref.(refClass{i}).refTxt{j};
            sInd        = ref.(refClass{i}).sInd(j);
            eInd        = ref.(refClass{i}).eInd(j);
            refVar      = ref.(refClass{i}).refVar{j};
            varName     = ref.(refClass{i}).varName{j};
            
            % Attempt to evaluate reference
            try
                refVal = eval(['data.(refClass{i}).' refVar]);
                value = [refTxt(1:sInd-1) '(' num2str(refVal) ')' refTxt(eInd+2:end)];
                value = eval(value);
                
                % Store value
                data.(refClass{i}).(varName) = value;
                nResolved = nResolved + 1;
                
                % Remove data from ref struct 
                if isequal(numel(ref.(refClass{i}).sInd),1) 
                    % Remove entire class if all references resolved
                    ref = rmfield(ref,refClass{i});
                else
                    % Remove reference properties once resolved
                    ref.(refClass{i}).refTxt(j) = [];
                    ref.(refClass{i}).sInd(j) = [];
                    ref.(refClass{i}).eInd(j) = [];
                    ref.(refClass{i}).refVar(j) = [];
                    ref.(refClass{i}).varName(j) = [];
                    ref.(refClass{i}).refLine(j) = [];
                end
                
                % Once a reference is resolved break out of loop to avoid 
                % index out of bounds error
                break;
                
            catch
            end
        end
    end
    numBadRef = numBadRef - nResolved;
    
    % If no progress has been made after completing a evaluation round
    % error has occured
    if isequal(nResolved,0)
        error('LOAD:refEvalFailure', 'Error evaluating references. Check for self referential variables.')
    end
    
end 
study = data.study;
del_labels = fieldnames(del.study);


for i = 1:numel(del_labels)
   study.(del_labels{i}) = del.study.(del_labels{i});
end

                      
end