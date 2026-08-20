
function out = load_live(filePath)
    outPlots = importdata(filePath);
    [ m, n ] = size(outPlots.data);
    
    out.t_sim = linspace(1,m,m);
    out.apgMean = outPlots.data(:,1);
    out.apgMin = outPlots.data(:,2);
    out.apgMax = outPlots.data(:,3);

    out.railExitMean = outPlots.data(:,7);
    out.railExitMin = outPlots.data(:,8);
    out.railExitMax = outPlots.data(:,9);
    
    out.minStabMean = outPlots.data(:,10);
    out.minStabMin = outPlots.data(:,11);
    out.minStabMax = outPlots.data(:,12);

    out.dRangeNMean = outPlots.data(:,13);
    dRangeNMin = outPlots.data(:,14);
    dRangeNMax = outPlots.data(:,15);

    out.dRangeEMean = outPlots.data(:,16);
    dRangeEMin = outPlots.data(:,17);
    dRangeEMax = outPlots.data(:,18);
    
    dRangeMMean = outPlots.data(:,19);
    dRangeMMin = outPlots.data(:,20);
    dRangeMMax = outPlots.data(:,21);

end