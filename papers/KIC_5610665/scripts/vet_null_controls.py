"""Reconstruct accepted null detections and apply the unchanged deterministic vetting.
This supplements SNR-only comparisons without rerunning BLS or using ML weights.
Usage: python vet_null_controls.py RAW_DIR NULL_RESULTS_DIR OUTPUT_JSON
"""
import json,sys
from pathlib import Path
import numpy as np
from null_controls import transform
from review_kepler import read_raw
from kepler_detector.preprocessing import preprocess
from kepler_detector.bls import Candidate,transit_mask
from kepler_detector.features import extract,transit_consistency
from kepler_detector.vetting import vet,priority_score
raw,info,cfg=read_raw(Path(sys.argv[1]));output=[]
for p in sorted(Path(sys.argv[2]).glob('*.json')):
 if p.name=='summary.json':continue
 trial=json.loads(p.read_text())
 if 'detections' not in trial:continue
 altered,_=transform(raw,trial['kind'],trial['seed']);curve=preprocess(altered,cfg);available=np.ones(len(curve.time),bool)
 for row in trial['detections']:
  c=Candidate(row['period_days'],row['duration_hours']/24,row['epoch_bkjd'],0,0,row['depth_ppm']/1e6,row['snr'],row['boundary'])
  residual=curve.subset(available);f=extract(residual,c);st=transit_consistency(residual,c);combined={**f,**st};v=vet(combined,cfg.min_snr,cfg.min_transits)
  assert abs(f['depth_snr']-row['snr'])<1e-7,(p.name,row)
  output.append({'kind':trial['kind'],'seed':trial['seed'],'order':row['order'],'period_days':c.period,'snr':f['depth_snr'],'transits':f['number_of_transits'],'priority':priority_score(combined,float('nan'),v['vetting_flags']),'max_event_contribution':st['max_single_transit_contribution'],**v})
  available&=~transit_mask(curve.time,c,row['mask_width'])
Path(sys.argv[3]).write_text(json.dumps(output,indent=2));print('Vetted',len(output),'accepted detections')
