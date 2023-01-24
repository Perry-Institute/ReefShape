# script to optimize camera distortion model and camera locations
# in metashape, based on deletion of bad tie points

# location for this file:
# Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts
# Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

# updated May 2022 by Will Greene / Perry Institute for Marine Science and
# Asif-ul Islam / Middlebury College for use with underwater photogrammetry
# updated Jan 2023 by Sam Marshall to reflect changes in API for Metashape 2.0.0

import Metashape

def gradSelectsOptimization():

    doc = Metashape.app.document
    chunk = doc.chunk

# define thresholds for reconstruction uncertainty and projection accuracy
    reconun = float(25)
    projecac = float(15)

# initiate filters, remote points above thresholds
    f = Metashape.TiePoints.Filter()
    f.init(chunk, Metashape.TiePoints.Filter.ReconstructionUncertainty)
    f.removePoints(reconun)

    f = Metashape.TiePoints.Filter()
    f.init(chunk, Metashape.TiePoints.Filter.ProjectionAccuracy)
    f.removePoints(projecac)

# optimize camera locations based on all distortion parameters
    chunk.optimizeCameras(fit_f=True, fit_cx=True, fit_cy=True,
                          fit_b1=True, fit_b2=True, fit_k1=True,
                          fit_k2=True, fit_k3=True, fit_k4=True,
                          fit_p1=True, fit_p2=True, fit_corrections=True,
                          adaptive_fitting=False, tiepoint_covariance=False)

    Metashape.app.update()
    print("Script finished")

label = "Custom/Optimize Cameras and Model"
Metashape.app.addMenuItem(label, gradSelectsOptimization)
