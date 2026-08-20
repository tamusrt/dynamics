%% Generic eng file creator
function e = create_eng(motor_name, m_total, wet_motor_mass,dry_motor_mass,diamGrain,diamPort,lenGrain)


    m_tot = m_total;
    eng.mFuel  = wet_motor_mass; %lbm %Total motor weight
    eng.dGrain = diamGrain; %in %Motor diameter 
    eng.dPort  = diamPort; %in %Motor Exit Diameter (Measurement or estimate)
    eng.lGrain = lenGrain ; %in %Total Motor Length
    propdat     = importdata(sprintf('%s/%s.dat',motor_name,motor_name)); %Generated from lengthy process involving openrocket
    dataPoints  = size(propdat.data,1); 
    eng.time = propdat.data(1:dataPoints,1);
    eng.mass = propdat.data(1:dataPoints,2) - dry_motor_mass;
    eng.thrust = propdat.data(1:dataPoints,3);
    eng.mLoss = m_tot - eng.mass;
    name =sprintf('%s/%s.mat',motor_name,motor_name);
    save(name,"eng",'-mat');
    e = eng;


end
