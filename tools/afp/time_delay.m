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

Filepath:
    /tools/afp/srt_fs_afp.m

Developers:
    (C) Aguilar Jaramillo, Alan     (20180329)
     
Description:
    Delays the execution of next batch of simulations by a predetermined
    amount of time to allow for the data acquisition system to gather data
    and run simulations with updated weather and fill tank information.

Input(s):
    t       -   Array containing time intervals
    j       -   Index of current time interval
    tsim    -   Time duration of last simulation
    
Output(s):
    NA
%}

function [ app ] = time_delay(t,j,tsim, app)
    fprintf('\nTime left for next sim: ');
    twait = tic;
    wait = true;
    i = 1;
    barMsg = sprintf("%2.0f%",t(j)/60);  
    fprintf('%s [mins]\n',barMsg); 
    app.TimefornextsimminEditField.Value = t(j)/60;

    while wait
        deltat = (toc(twait) + tsim)/60;
        if ((deltat - i) > 0)
            nBack  = 10;
            fprintf(repmat('\b',1,nBack)); 
            tleft = (t(j)/60) - i;
            barMsg = sprintf("%2.0f%",tleft);  
            fprintf('%s [mins]\n',barMsg);
            app.TimefornextsimminEditField.Value = tleft;
            drawnow

            i = i + 1;
        end
        if i > (t(j)/60)      
            wait = false;
            fprintf('      Starting new sim...\n');
        end
    end 
end