"""Small reproducible checks: fixed-ephemeris subsets, stellar duration, sector windows.
Run after review_kepler.py. Usage: python supplementary_checks.py REVIEW_OUTPUT_DIR
"""
import sys,json,csv,gzip,hashlib,datetime
from pathlib import Path
import numpy as np
from astropy.constants import G,M_sun,R_sun
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from kepler_detector.mast import load_curve
from kepler_detector.bls import Candidate
from kepler_detector.features import extract
root=Path(__file__).resolve().parents[1];out=root/'data/kepler_review'
c=load_curve(Path(sys.argv[1])/'candidate05_residual.npz')
p=215.39750429110552;e=260.54511369116005;d=13.4316/24;k=Candidate(p,d,e,0,0,75.17032342807539e-6,10.028094622665412)
results=[]
for name,drop in [('all',[]),('without_cycles_0_2',[0,2]),('without_partial_cycles_3_4',[3,4]),('only_cycles_0_2_drop_other_event_windows',[3,4,5,6])]:
 keep=np.ones(len(c.time),bool)
 for n in drop:keep&=abs(c.time-(e+n*p))>3*d
 f=extract(c.subset(keep),k);results.append({'name':name,'dropped_cycles':drop,'depth_ppm':f['depth_ppm'],'snr':f['depth_snr'],'n_inside':f['number_of_in_transit_points'],'uncertainty_ppm':f['depth_ppm']/f['depth_snr']})
(out/'fixed_ephemeris_subsets.json').write_text(json.dumps(results,indent=2))
row=next(r for r in csv.DictReader((root/'data/catalog_checks/stellar.csv').open()) if r['st_delivname']=='q1_q17_dr25_stellar')
period=p*86400;mass=float(row['mass']);radius=float(row['radius']);a=(G.value*mass*M_sun.value*period**2/(4*np.pi**2))**(1/3)
duration=period/np.pi*np.arcsin(radius*R_sun.value/a)/3600
(out/'duration_check.json').write_text(json.dumps({'source':'catalog_checks/stellar.csv: q1_q17_dr25_stellar','mass_solar':mass,'radius_solar':radius,'central_circular_small_planet_duration_hours':duration,'observed_box_duration_hours':13.4316,'assumptions':'circular orbit, central transit, negligible planet radius; no covariance or uncertainty propagation; BLS box duration is not a fitted contact duration'},indent=2))
