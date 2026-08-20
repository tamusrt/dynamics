// STAR-CCM+ macro: CFDmacro2019.java
// Written by STAR-CCM+ 12.04.011
package macro;

import java.util.*;

import star.common.*;
import star.base.neo.*;
import star.resurfacer.*;
import star.prismmesher.*;
import star.meshing.*;
import star.base.report.*;
import star.flow.*;
import star.energy.*;

public class CFDmacro2019 extends StarMacro 
{
	public void execute() 
	{
		Simulation simulation_0=getActiveSimulation();
		simulation_0.saveState("D:\\Theseus2c_CFD.sim");
		templateActivity(0.1, 34.6932667025, 34.6879827499, 0.0, 0.605480991129, 0.999847695156, 0.0, 0.0174524064373, -0.0174524064373, 0.0, 0.999847695156, 100618.905072, -706.094927616, 1.17035397159, 299.401197605, 1.66191493123, 31.0, 0.10471975512, "D:\\Theseus_CFD_M0.1_A1.0B_0.0.sim");
		
	}

	private void templateActivity(
		double machNumberConstant,
		double refrenceVelocity,
		double gasVelocityX,
		double gasVelocityY,
		double gasVelocityZ,
		double WindXx,
		double WindXy,
		double WindXz,
		double WindZx,
		double WindZy,
		double WindZz,
		double FreeStreamPressure,
		double refrencePressure,
		double refrenceDensity,
		double staticTemperature,
		double BoundaryLayerHeight,
		double Ncells,
		double WakeAngle,
		String filename)
	{
		Simulation simulation_0 = getActiveSimulation();
		
		AutoMeshOperation autoMeshOperation_0 = ((AutoMeshOperation) simulation_0.get(MeshOperationManager.class).getObject("Automated Mesh"));
		SurfaceCustomMeshControl surfaceCustomMeshControl_0 = ((SurfaceCustomMeshControl) autoMeshOperation_0.getCustomMeshControls().getObject("Tip Control"));
		
		surfaceCustomMeshControl_0
			.getCustomValues()
			.get(PartsWakeRefinementValuesManager.class)
			.getDirection()
			.setComponents(WindXx, WindXy, WindXz);
		
		surfaceCustomMeshControl_0
			.getCustomValues()
			.get(PartsWakeRefinementValuesManager.class)
			.getSpreadAngle()
			.setValue(WakeAngle);

		MeshPipelineController meshPipelineController_0 = 
      			simulation_0.get(MeshPipelineController.class);

    		meshPipelineController_0.generateVolumeMesh();
		
		
		PhysicsContinuum physicsContinuum_0 = ((PhysicsContinuum) simulation_0.getContinuumManager().getContinuum("Physics 1"));
		Region region_0 = simulation_0.getRegionManager().getRegion("HalfSphere");
		Boundary boundary_0 = region_0.getBoundaryManager().getBoundary("Free Stream");
		
		physicsContinuum_0
			.getInitialConditions()
			.get(VelocityProfile.class)
			.getMethod(ConstantVectorProfileMethod.class)
			.getQuantity()
			.setComponents(gasVelocityX, gasVelocityY, gasVelocityZ);

		region_0
			.get(RegionInitialConditionManager.class)
			.get(VelocityProfile.class)
			.getMethod(ConstantVectorProfileMethod.class)
			.getQuantity()
			.setComponents(gasVelocityX, gasVelocityY, gasVelocityZ);

		boundary_0
			.getValues()
			.get(MachNumberProfile.class)
			.getMethod(ConstantScalarProfileMethod.class)
			.getQuantity()
			.setValue(machNumberConstant);
		
		FlowDirectionProfile flowDirectionProfile_0 = boundary_0.getValues().get(FlowDirectionProfile.class);
		flowDirectionProfile_0.getMethod(ConstantVectorProfileMethod.class).getQuantity().setComponents(WindXx, WindXy, WindXz);
		
		ForceCoefficientReport forceCoefficientReport_0 = ((ForceCoefficientReport) simulation_0.getReportManager().getReport("Drag Coefficient"));
		ForceCoefficientReport forceCoefficientReport_1 = ((ForceCoefficientReport) simulation_0.getReportManager().getReport("Lift Coefficient"));
		
		forceCoefficientReport_0.getDirection().setComponents(WindXx, WindXy, WindXz);
		forceCoefficientReport_1.getDirection().setComponents(WindZx, WindZy, WindZz);
		
		forceCoefficientReport_0.getReferenceVelocity().setValue(refrenceVelocity);
		forceCoefficientReport_1.getReferenceVelocity().setValue(refrenceVelocity);
		
		region_0.get(RegionInitialConditionManager.class).get(InitialPressureProfile.class).getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(refrencePressure);
		physicsContinuum_0.getInitialConditions().get(InitialPressureProfile.class).getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(refrencePressure);
		boundary_0.getValues().get(StaticPressureProfile.class).getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(refrencePressure);
		
		((ForceReport) simulation_0.getReportManager().getReport("X Force")).getReferencePressure().setValue(refrencePressure);
		((ForceReport) simulation_0.getReportManager().getReport("Z Force")).getReferencePressure().setValue(refrencePressure);
		
		forceCoefficientReport_0.getReferencePressure().setValue(refrencePressure);
		forceCoefficientReport_1.getReferencePressure().setValue(refrencePressure);
		
		forceCoefficientReport_0.getReferenceDensity().setValue(refrenceDensity);
		forceCoefficientReport_1.getReferenceDensity().setValue(refrenceDensity);

		forceCoefficientReport_0.getDirection().setComponents(WindXx, WindXy, WindXz);
		forceCoefficientReport_1.getDirection().setComponents(WindZx, WindZy, WindZz);
		
		ForceReport forceReport_0 = ((ForceReport) simulation_0.getReportManager().getReport("X Force"));
		forceReport_0.getDirection().setComponents(WindXx, WindXy, WindXz);

		ForceReport forceReport_1 = ((ForceReport) simulation_0.getReportManager().getReport("Z Force"));
		forceReport_1.getDirection().setComponents(WindZx, WindZy, WindZz);
		
		region_0.get(RegionInitialConditionManager.class).get(StaticTemperatureProfile.class).getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(staticTemperature);
		boundary_0.getValues().get(StaticTemperatureProfile.class).getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(staticTemperature);
		physicsContinuum_0.getInitialConditions().get(StaticTemperatureProfile.class).getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(staticTemperature);
		
		simulation_0.saveState(filename);
	}
}
