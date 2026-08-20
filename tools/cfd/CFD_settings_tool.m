Init=menu('What action would you like to perform?','Boundary layer calculator','Physics conditions from FS data','physics conditions from mach number');
if Init==1
    a=input('what is the boundry stretching value: ');
    ds=input('what is the ds value from the y+ calculator in inches: ');
    x=10:1:100;
    H=zeros(1,numel(x));
    for Val=1:1:numel(x)
        H(Val)=2*ds*((a^(x(Val)+1)-1)/(a-1));
    end
    table(x',H')
    fprintf('H is in inches \n')
    
    
elseif Init==2
    %Calculates physics conditions using an FS out file
    Dim=size(out.mach);

    T0=278.41;          %K
    MuRef=1.716*10^-5;  %kg/(m*s)
    S=110.4;            %K

    R=287;              %J*kg^-1*K^-1
    SHR=1.4;
    Roe=out.rho(2:Dim(1),2:Dim(2)).*(3.281*3.281*3.281*14.5939); %kg*m^-3
    L=136.4/(12*3.281); %m

    syms D M RE
    f(D,M,RE)=((D.*M.*sqrt(SHR.*R.*T0.^3).*S.*L)./((RE.*MuRef.*(T0+S))-(D.*M.*sqrt(SHR.*R.*T0.^3).*L)));

    TempRe=eval(f(Roe,out.mach(2:Dim(1),2:Dim(2)),out.Re(2:Dim(1),2:Dim(2))));
    
    Dimp=size(TempRe);
    Mavg=zeros(1,Dimp(1));
    Tavg=zeros(1,Dimp(1));
    Reavg=zeros(1,Dimp(1));
    Rhoavg=zeros(1,Dimp(1));
    for i=1:Dimp(1)-1
        hold on
        Mavg(i)=mean(out.mach(i+1,2:Dimp(2)));
        Reavg(i)=mean(out.Re(i+1,2:Dimp(2)));
        Tavg(i)=mean(TempRe(i,:));
        Rhoavg(i)=mean(out.rho(i+1,2:Dimp(2)))*(3.281*3.281*3.281*14.5939);
    end
    Pavg=Rhoavg.*R.*Tavg;
    Vavg=Mavg.*sqrt(1.4*287.*Tavg);
    figure()
    for i=1:99
        hold on
        plot(out.mach(2:Dim(1),i+1),TempRe(:,i),'c-')
        xlabel('Mach no.')
        ylabel('Temp [k]')
    end
    plot(Mavg,Tavg,'LineWidth',4)
    grid on
    
    %table
    table(Mavg',Reavg',Tavg',Rhoavg',Pavg')
    
    
elseif Init==3
    %calculates physics using ideal gas model, user mach number
    MachNum=abs(input('What is the mach number of the run? '));
    T0=300;     %K
    P0=101325;  %Pa
    
    ToT=1+(0.2*(MachNum^2));
    T=T0/ToT;
    P=P0/((ToT)^(1.4/(1.4-1)));
    Rho=P/(287.16*T);
    V=MachNum*sqrt(1.4*287.16*T);
    
    fprintf('Mach Number: %f\n',MachNum)
    fprintf('Velocity [m/s]: %f\n', V)
    fprintf('Static Temperature [K]: %f\n',T)
    fprintf('Static Pressure [Pa]: %f\n',P)
    fprintf('Static Guage Pressure [Pa]: %f\n', P-P0)
    fprintf('Static Density [kg*m^-3]: %f\n\n', Rho)
    
end
    