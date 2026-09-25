"""Audit original Kepler candidates; fixed ephemeris, no independent discovery.
Usage: PYTHONPATH=/path/to/repository python review_kepler.py RAW_DIR OUTPUT_DIR
"""
import sys,json,csv
from pathlib import Path
import numpy as np
from astropy.io import fits
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from config import Config
from kepler_detector.mast import Curve,save_curve
from kepler_detector.preprocessing import preprocess
from kepler_detector.bls import Candidate,transit_mask,phase
from kepler_detector.features import extract,transit_consistency

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_MASK=1130799

def read_raw(folder):
 metadata=json.loads((ROOT/'data/metadata_sanitized.json').read_text())
 arrays=[];info=[]
 for index,prod in enumerate(metadata['source']['products']):
  path=folder/prod['filename']
  with fits.open(path) as h:
   d=h[1].data;t=np.asarray(d['TIME'],float);y=np.asarray(d['PDCSAP_FLUX'],float);e=np.asarray(d['PDCSAP_FLUX_ERR'],float);q=d['SAP_QUALITY']
   good=((q&DEFAULT_MASK)==0)&np.isfinite(t)&np.isfinite(y)&(y>0)
   med=np.median(y[good]);yn=y[good]/med;en=e[good]/med
   fallback=max(1e-7,np.nanmedian(abs(np.diff(yn)))/.954)
   en[~np.isfinite(en)|(en<=0)]=fallback
   arrays.append((t[good],yn,en,np.full(sum(good),index)))
   info.append(dict(index=index,quarter=h[0].header['QUARTER'],module=h[0].header['MODULE'],channel=h[0].header['CHANNEL'],path=str(path),all_time=t,good=good,quality=q.copy()))
 a=[np.concatenate([x[i] for x in arrays]) for i in range(4)];order=np.argsort(a[0]);a=[x[order] for x in a];unique=np.r_[True,np.diff(a[0])>1e-8]
 return Curve(*(x[unique] for x in a),metadata['source']),info,Config(**metadata['config'])

def from_row(r):
 return Candidate(float(r['period_days']),float(r['duration_hours'])/24,float(r['epoch_bkjd']),0,0,float(r['depth_ppm'])/1e6,float(r['depth_snr']))

def main():
 folder=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
 raw,info,config=read_raw(folder);curve=preprocess(raw,config)
 save_curve(raw,out/'raw_reconstructed.npz');save_curve(curve,out/'preprocessed_reconstructed.npz')
 rows=sorted(list(csv.DictReader((ROOT/'data/detections_original.csv').open())),key=lambda r:int(r['detection_order']))
 available=np.ones(len(curve.time),bool);audit=[];mask_before={}
 for r in rows:
  c=from_row(r);res=curve.subset(available);mask_before[int(r['detection_order'])]=available.copy()
  f=extract(res,c);st=transit_consistency(res,c)
  audit.append({'order':int(r['detection_order']),'period':c.period,'original_points':int(float(r['number_of_points'])),'reconstructed_points':len(res.time),'original_depth_ppm':float(r['depth_ppm']),'reconstructed_depth_ppm':f['depth_ppm'],'original_snr':float(r['depth_snr']),'reconstructed_snr':f['depth_snr'],'consistency':st})
  available&=~transit_mask(curve.time,c,float(r['mask_width_duration']))
 principal=from_row(rows[-1]);res=curve.subset(mask_before[5]);save_curve(res,out/'candidate05_residual.npz')
 events=[];window_rows=[];cad=float(np.median(np.diff(raw.time)))
 # Edges use quality-filtered finite photometry, gaps >0.5 d and product boundaries.
 split=np.r_[0,np.flatnonzero((np.diff(raw.time)>.5)|(np.diff(raw.segment)!=0))+1,len(raw.time)]
 segments=[(raw.time[a]-cad/2,raw.time[b-1]+cad/2) for a,b in zip(split[:-1],split[1:])]
 rb=[]
 for cycle in range(7):
  center=principal.epoch+cycle*principal.period;lo=center-principal.duration/2;hi=center+principal.duration/2
  product=min(info,key=lambda x:np.nanmin(abs(x['all_time']-center)))
  t=product['all_time'];g=product['good'];nvalid=int(sum(g&(t>lo)&(t<hi)));slot=np.isfinite(t)&(t>lo)&(t<hi)
  nres=int(sum(abs(res.time-center)<principal.duration/2))
  coverage=float(min(1,np.maximum(0,np.minimum(raw.time+cad/2,hi)-np.maximum(raw.time-cad/2,lo)).sum()/principal.duration))
  edge=min(abs(center-v) for pair in segments for v in pair)
  enclosing=next((pair for pair in segments if pair[0]<=center<=pair[1]),None)
  rec={'cycle':cycle,'center_bkjd':center,'quarter':product['quarter'],'module':product['module'],'channel':product['channel'],'n_slots':int(sum(slot)),'n_valid_default':nvalid,'n_after_iterative_masks':nres,'coverage_fraction':coverage,'nearest_segment_edge_hours':edge*24,'center_inside_segment':enclosing is not None,'quality_values':json.dumps(np.unique(product['quality'][slot]).tolist())}
  events.append(rec)
  use=abs(res.time-center)<2
  outside=use&(abs(res.time-center)>=principal.duration)&(abs(res.time-center)<=3*principal.duration)
  base=float(np.median(res.flux[outside])) if sum(outside)>=4 else 1.
  for ti,yi,ei in zip(res.time[use],res.flux[use],res.error[use]):
   m=-principal.depth*1e6 if abs(ti-center)<principal.duration/2 else 0.
   window_rows.append({'cycle':cycle,'bkjd':ti,'hours_from_center':(ti-center)*24,'flux_ppm':(yi-base)*1e6,'pipeline_error_ppm':ei*1e6,'fixed_box_ppm':m,'residual_ppm':(yi-base)*1e6-m})
  tpf=list(folder.glob(product['path'].split('/')[-1].replace('_llc.fits','_lpd-targ.fits.gz')))
  if tpf:
   with fits.open(tpf[0]) as h:
    d=h[1].data;tt=np.asarray(d['TIME'],float);q=d['QUALITY'];within=np.isfinite(tt)&(abs(tt-center)<principal.duration/2)
    aper_rows=np.any((h[2].data&2)!=0,axis=1)
    levels=np.asarray(d['RB_LEVEL'],float)[:,aper_rows,:]
    for j in range(levels.shape[-1]):
     v=levels[within,:,j];finite=v[np.isfinite(v)&(v>=0)]
     rb.append({'cycle':cycle,'quarter':product['quarter'],'duration_cadences':int(h[1].header[f'RBTDUR{j+1}']),'n_window':int(sum(within)),'n_default':int(sum(within&((q&DEFAULT_MASK)==0))),'rolling_aperture_flag_n':int(sum(within&((q&131072)!=0))),'rolling_mask_flag_n':int(sum(within&((q&262144)!=0))),'rb_valid_cells':len(finite),'rb_max':float(np.max(finite)) if len(finite) else None,'rb_nonzero_cells':int(sum(finite>0))})
 def write(name,records):
  with (out/name).open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=records[0].keys());w.writeheader();w.writerows(records)
 write('events.csv',events);write('rolling_band.csv',rb);write('event_samples.csv',window_rows)
 first=from_row(rows[0]);m1=transit_mask(curve.time,principal);m2=transit_mask(curve.time,first);sec=Candidate(first.period,first.duration,first.epoch+first.period/2,0,0,0,0)
 overlap={'relative_period_difference_percent':100*abs(principal.period-first.period)/principal.period,'primary_shared_cadences':int(sum(m1&m2)),'secondary_shared_cadences':int(sum(m1&transit_mask(curve.time,sec))),'masked_by_first_at_recorded_width':int(sum(m1&transit_mask(curve.time,first,float(rows[0]['mask_width_duration'])))),'centers_nearest_primary_days':[float(abs(phase([e['center_bkjd']],first.period,first.epoch)[0])) for e in events],'centers_nearest_secondary_days':[float(abs(phase([e['center_bkjd']],first.period,sec.epoch)[0])) for e in events]}
 (out/'reconstruction.json').write_text(json.dumps({'raw_points':len(raw.time),'preprocessed_points':len(curve.time),'audit':audit,'overlap':overlap},indent=2))
 # All six observed events share axes; orange box fixed to exported parameters, not refitted.
 fig,axes=plt.subplots(6,2,figsize=(12,14),sharex=True,sharey=True,layout='constrained')
 for axrow,cyc in zip(axes,[0,2,3,4,5,6]):
  rr=[r for r in window_rows if r['cycle']==cyc];x=np.array([r['hours_from_center'] for r in rr]);y=np.array([r['flux_ppm'] for r in rr]);model=np.array([r['fixed_box_ppm'] for r in rr]);ev=events[cyc]
  for j,ax in enumerate(axrow):
   z=y if j==0 else y-model;ax.scatter(x,z,s=8,alpha=.35,color='#355c83');ax.axvspan(-principal.duration*12,principal.duration*12,color='#80c9b2',alpha=.2);ax.axhline(0,color='gray',lw=.6)
   for left in np.arange(-48,48,3):
    b=(x>=left)&(x<left+3)
    if sum(b)>0:ax.plot(left+1.5,np.mean(z[b]),'o',color='black',ms=4)
   ax.set_xlim(-48,48);ax.set_ylim(-350,350);ax.set_title(f'Ciclo {cyc} · Q{ev["quarter"]} · '+('flujo' if j==0 else 'residuo'))
  xx=np.array([-48,-principal.duration*12,-principal.duration*12,principal.duration*12,principal.duration*12,48]);axes[list([0,2,3,4,5,6]).index(cyc),0].plot(xx,[0,0,-principal.depth*1e6,-principal.depth*1e6,0,0],color='#d36b2b')
 for ax in axes[:,0]:ax.set_ylabel('ppm')
 for ax in axes[-1]:ax.set_xlabel('Horas respecto al centro nominal')
 fig.suptitle('KIC 5610665 · eventos Kepler y residuos de caja fija\nPuntos: PDCSAP reprocesado y enmascarado; negro: medias de 3 h; ventana ±48 h')
 fig.savefig(out/'kepler_events.png',dpi=150);plt.close(fig)
 # Folded view with original 75 ppm model; no independent refit.
 ph=phase(res.time,principal.period,principal.epoch)*24;use=abs(ph)<48
 f=extract(res,principal);base=np.median(res.flux[abs(ph)>principal.duration*36]);y=(res.flux-base)*1e6
 fig,axs=plt.subplots(2,1,figsize=(10,7),sharex=True,layout='constrained')
 mod=np.where(abs(ph)<principal.duration*12,-principal.depth*1e6,0)
 for j,ax in enumerate(axs):
  yy=y if j==0 else y-mod;ax.scatter(ph[use],yy[use],s=7,alpha=.18)
  for left in np.arange(-48,48,1.5):
   b=(ph>=left)&(ph<left+1.5)
   if sum(b)>1:ax.errorbar(left+.75,np.mean(yy[b]),yerr=np.std(yy[b],ddof=1)/np.sqrt(sum(b)),fmt='o',ms=4,color='black')
  ax.axhline(0,color='gray',lw=.7);ax.axvspan(-principal.duration*12,principal.duration*12,color='#80c9b2',alpha=.2);ax.set_ylabel('Flujo (ppm)' if j==0 else 'Residuo (ppm)');ax.set_ylim(-300,300)
 axs[0].plot(xx,[0,0,-principal.depth*1e6,-principal.depth*1e6,0,0],color='#d36b2b');axs[1].set_xlabel('Horas desde el centro de fase');axs[0].set_title('KIC 5610665 · P = 215,3975 d · caja exportada, sin nuevo ajuste\nBarras: error estándar descriptivo por bin; no incluye ruido correlacionado')
 fig.savefig(out/'kepler_folded.png',dpi=150);plt.close(fig)
 print(json.dumps({'events':events,'overlap':overlap,'audit':audit},indent=2))
if __name__=='__main__':main()
