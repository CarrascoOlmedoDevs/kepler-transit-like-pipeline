"""Exploratory null controls using unchanged Sakanet BLS/feature/masking code.
Usage: python null_controls.py RAW_DIR OUT_DIR [N_SHUFFLES=8]
Not the DR25 implementation or a calibrated planet/FAP estimator.
"""
import sys,json,time
from pathlib import Path
import numpy as np
from concurrent.futures import ProcessPoolExecutor,as_completed
from review_kepler import read_raw
from kepler_detector.mast import Curve
from kepler_detector.preprocessing import preprocess
from kepler_detector.bls import search,duplicate
from kepler_detector.features import extract,transit_consistency
from scanner_masking_snapshot import adaptive_mask

def transform(raw,kind,seed):
 if kind=='nominal':return raw,[]
 if kind=='inverted':return Curve(raw.time,2-raw.flux,raw.error,raw.segment,raw.metadata),[]
 # Reorder whole quarter blocks, preserving all samples and within-block cadence/gaps.
 # Keep the original sequence of inter-quarter gaps; the observing window changes.
 rng=np.random.default_rng(seed);ids=np.unique(raw.segment)
 chronological=sorted(ids,key=lambda i:raw.time[raw.segment==i].min())
 blocks=[np.flatnonzero(raw.segment==i) for i in chronological]
 gaps=[raw.time[blocks[j+1][0]]-raw.time[blocks[j][-1]] for j in range(len(blocks)-1)]
 order=rng.permutation(len(blocks));cursor=raw.time.min();parts=[]
 for j,k in enumerate(order):
  ix=blocks[k];t=raw.time[ix]-raw.time[ix[0]]+cursor
  parts.append((t,raw.flux[ix],raw.error[ix],np.full(len(ix),j)))
  cursor=t[-1]+(gaps[j] if j<len(gaps) else 0)
 return Curve(*(np.concatenate([p[i] for p in parts]) for i in range(4)),raw.metadata),[int(chronological[k]) for k in order]

def trial(rawdir,outdir,kind,seed):
 start=time.monotonic();raw,info,cfg=read_raw(Path(rawdir));raw,order=transform(raw,kind,seed)
 curve=preprocess(raw,cfg);available=np.ones(len(curve.time),bool);found=[];rows=[];reason='max_detections reached'
 for iteration in range(cfg.max_planets):
  res=curve.subset(available)
  if len(res.time)<max(100,.2*len(curve.time)):reason='insufficient residual data';break
  peaks,_=search(res,cfg);selected=None
  for peak in peaks:
   try:f=extract(res,peak);st=transit_consistency(res,peak)
   except ValueError:continue
   selected=peak;break
  if selected is None:reason='no measurable peaks';break
  if f['depth_snr']<cfg.min_snr or f['number_of_transits']<cfg.min_transits:reason='below SNR/event threshold';break
  repeated=any(duplicate(selected,old,curve.time) for old in found)
  available,effectiveness,width,removed=adaptive_mask(curve,available,selected,target_effectiveness=.9 if repeated else .8,min_width=2 if repeated else 1.5,max_removed_fraction=.2 if repeated else .15)
  rows.append({'order':iteration+1,'period_days':selected.period,'epoch_bkjd':selected.epoch,'duration_hours':24*selected.duration,'depth_ppm':f['depth_ppm'],'snr':f['depth_snr'],'transits':f['number_of_transits'],'boundary':selected.duration_at_boundary,'mask_width':width,'max_event_contribution':st['max_single_transit_contribution']});found.append(selected)
  print(kind,seed,'iteration',iteration+1,'P',selected.period,'SNR',f['depth_snr'],flush=True)
 data={'kind':kind,'seed':seed,'quarter_segment_permutation':order,'config':cfg.to_dict(),'preprocessed_points':len(curve.time),'stop_reason':reason,'seconds':time.monotonic()-start,'detections':rows,'max_snr_150_300':max([r['snr'] for r in rows if 150<=r['period_days']<=300],default=None),'threshold':10.028094622665412,'interpretation':'exploratory controls; quarter exchangeability unproven; not calibrated FAP; no ML applied'}
 Path(outdir,f'{kind}_{seed}.json').write_text(json.dumps(data,indent=2));return data

if __name__=='__main__':
 rawdir,outdir=sys.argv[1:3];n=int(sys.argv[3]) if len(sys.argv)>3 else 8;Path(outdir).mkdir(parents=True,exist_ok=True)
 jobs=[('nominal',42),('inverted',42)]+[('shuffled',42+i) for i in range(n)]
 result=[]
 with ProcessPoolExecutor(max_workers=2) as pool:
  futures=[pool.submit(trial,rawdir,outdir,k,s) for k,s in jobs]
  for future in as_completed(futures):
   result.append(future.result());Path(outdir,'summary.json').write_text(json.dumps(result,indent=2));print('FINISHED',result[-1]['kind'],result[-1]['seed'],flush=True)
