
close all
mc_range = 1:10;


alpha = out.alpha(:, mc_range);
beta = out.beta(:, mc_range);
mach = out.mach(:, mc_range);
re = out.Re(:, mc_range);


poweron = 1550; % lt this value
thrust_cutoff = 1670;

alpha_poweron = alpha(124:poweron, :);
beta_poweron = alpha(124:poweron, :);
mach_poweron = mach(124:poweron, :);
subplot(2, 2, 1);
l = plot(mach_poweron, alpha_poweron, 'LineWidth', .1, 'Color', [1, 0, 0, .3]);
% l.Color(4) = 0.5;
title("alpha poweron")
xlim([.08, 1.5]);
xlabel("Mach [-]");
ylabel("Alpha [deg]");


subplot(2, 2, 3);
l = plot(mach_poweron, beta_poweron, 'LineWidth', .1, 'Color', [1, 0, 0, .3]);
% l.Color(4) = 0.5;
title("beta poweron")
xlim([.08, 1.5]);
alpha_poweron = alpha(thrust_cutoff:end, :);
beta_poweron = beta(thrust_cutoff:end, :);
mach_poweron = mach(thrust_cutoff:end, :);
xlabel("Mach [-]");
ylabel("Alpha [deg]");


subplot(2, 2, 2);
l = plot(mach_poweron, alpha_poweron, 'LineWidth', .1, 'Color', [0, 0, 1, .3]);
% l.Color(4) = 0.5;
title("alpha powerof")
xlim([.08, 1.5]);
xlabel("Mach [-]");
ylabel("Alpha [deg]");


subplot(2, 2, 4);
l = plot(mach_poweron, beta_poweron,'LineWidth', .1, 'Color', [0, 0, 1, .3]);
% l.Color(4) = 0.5;
title("beta poweroff")
xlim([.08, 1.5]);
%xticks(0:0.1:.8);
%plot(mach, beta);
xlabel("Mach [-]");
ylabel("Alpha [deg]");

fig = figure;
re = re(:, :);
mach_poweron = mach(thrust_cutoff:end, :);
plot(mach, re,'LineWidth', .1, 'Color', [0, 0, 1, .3]);
xlabel("Mach");
ylabel("Re");
title("Reynolds number over Mach");
srt_fs_format
