% takes an output file as input and computes the mean of the specified
% values, outputting them to a CSV
% dev Nacho Durante 2023-2024



[fileName,fileDir,~] = uigetfile('../../output/*.mat*','Select Data File');
filePath = [fileDir fileName];  
archive = load(filePath);
out     = archive.out;
inp     = archive.inp;

positions = [mean(out.tPos(:,:,1),2) mean(out.tPos(:,:,2),2) mean(out.tPos(:,:,3),2)];
velocities = [mean(out.tVel(:,:,1),2) mean(out.tVel(:,:,2),2) mean(out.tVel(:,:,3),2)];
accels = [mean(out.tAcc(:,:,1),2) mean(out.tAcc(:,:,2),2) mean(out.tAcc(:,:,3),2)];

velocities = [velocities, sqrt(sum(velocities.^2,2))];
accels = [accels, sqrt(sum(accels.^2,2))];
stability = mean(out.stab(:,:),2);


data = [positions, velocities, accels, stability];
titles = {'x-pos' 'y-pos' 'z-pos' 'x-vel' 'y-vel' 'z-vel' 'net-vel' 'x-accel' 'y-accel' 'z-accel' 'net-accel' 'stability'};
c = [titles ; num2cell(data)];
fileName = "" + fileName;
writecell(c,fileName.extractBetween(1, fileName.strlength() - 3) + ".csv");





