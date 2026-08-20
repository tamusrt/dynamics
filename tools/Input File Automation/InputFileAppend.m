function InputFileAppend(filename, fname)
%% Writes RASAero alpha tests to FS input file
% For use with InputFileCreator
% dev: 04/10/2021 Sarah Kinney
% mod: 11/24/2023 Nacho Durante

%% Constants
mach = 1.5;
mach_q = mach/0.01;

%% RAS Data Import
RAS_subsonic = importdata(fname, ' ', 0);
RAS_transonic = importdata(fname, ' ', 77);     % Only use up to line 558 (inclusive)
RAS_supersonic1 = importdata(fname, ' ', 728);  
RAS_supersonic2 = importdata(fname, ' ', 902);  % Only use up to line 132 (inclusive)
%disp(RAS_subsonic.textdata);
%disp(RAS_transonic.textdata);
%disp(RAS_supersonic1.textdata);
%disp(RAS_supersonic2.textdata);

col_len = size(RAS_supersonic1.data, 2);
row_len = size(RAS_subsonic(1:78,:), 1) + size(RAS_transonic.data(1:558, :), 1) + size(RAS_supersonic1.data, 1) + size(RAS_supersonic2.data(1:132, :), 1);

sub_r_len = size(RAS_subsonic(1:78,:), 1);
sub_c_len = size(RAS_subsonic, 2);
trans_r_len = size(RAS_transonic.data(1:558, :), 1);
trans_c_len = size(RAS_transonic.data(1:558, :), 2);
sup1_r_len = size(RAS_supersonic1.data, 1);
sup1_c_len = size(RAS_supersonic1.data, 2);
sup2_r_len = size(RAS_supersonic2.data(1:132, :), 1);
sup2_c_len = size(RAS_supersonic2.data(1:132, :), 2);

RAS = zeros(row_len, col_len);
RAS(1:sub_r_len, 1:sub_c_len) = RAS_subsonic(1:78,:);
row = sub_r_len;
RAS(row + 1: row + trans_r_len, 1:trans_c_len) = RAS_transonic.data(1:558, :);
row = row + trans_r_len;
RAS(row + 1: row + sup1_r_len, 1:sup1_c_len) = RAS_supersonic1.data();
row = row + sup1_r_len;
RAS(row + 1: row + sup2_r_len, 1:sup2_c_len) = RAS_supersonic2.data(1:132, :);

%% Parse Necessary RAS Data
new_RAS = zeros(mach_q, 7);
row = 1;
for i = 1:6:length(RAS)
    new_RAS(row, 1) = RAS(i+2, 2); % alpha
    alpha = RAS(i+2, 2);
    new_RAS(row, 2) = RAS(i, 1); % mach
    new_RAS(row, 3) = RAS(i+1, 3); % xCP
    new_RAS(row, 4) = RAS(i+4, 5)*cosd(alpha) - RAS(i+4, 6)*sind(alpha); % CL Coast / power off
    new_RAS(row, 5) = RAS(i+4, 4); % CD Coast / power off
    new_RAS(row, 6) = RAS(i+5, 5)*cosd(alpha) - RAS(i+4, 6)*sind(alpha); % CL Boost / power on
    new_RAS(row, 7) = RAS(i+5, 4); % CD Boost / power on
    row = row + 1;
end

%% Write New Input File
fid = fopen(filename, 'a+');

for i = 1:mach_q
    for j = 1:7
        fprintf(fid, '%f	', new_RAS(i, j));
    end
    fprintf(fid, '\n');
    
end
fid = fclose(fid);