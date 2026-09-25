"""Seasonal depth audit using exported event diagnostics and original FITS headers.
Usage: python seasonal_consistency.py RAW_FITS_DIRECTORY
Errors are the internal white-noise diagnostic errors, not calibrated total errors.
"""
import sys,csv,json,hashlib
from pathlib import Path
from collections import Counter
import numpy as np
from scipy.stats import chi2
from astropy.io import fits
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
root=Path(__file__).resolve().parents[1];out=root/'data/seasonal_review';out.mkdir(exist_ok=True)
raw=Path(sys.argv[1]);headers=[]
for p in sorted(raw.glob('*llc.fits')):
 with fits.open(p) as h:
  a,b=h[0].header,h[1].header
  headers.append({'filename':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'quarter':int(a['QUARTER']),'season':int(a['SEASON']) if 'SEASON' in a else None,'module':int(a['MODULE']),'channel':int(a['CHANNEL']),'CROWDSAP':float(b['CROWDSAP']),'FLFRCSAP':float(b['FLFRCSAP'])})
headers=sorted(headers,key=lambda x:x['quarter']);byq={r['quarter']:r for r in headers}
def write_csv(name,rows):
 with (out/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
write_csv('quarter_headers.csv',headers)
audit=json.loads((root/'data/kepler_review/reconstruction.json').read_text())['audit'][-1]['consistency']
depths=json.loads(audit['individual_transit_depths']);snrs=json.loads(audit['individual_transit_snr'])
events=list(csv.DictReader((root/'data/kepler_review/events.csv').open()));observed=[e for e in events if int(e['n_after_iterative_masks'])>0];rows=[]
for e,d,s in zip(observed,depths,snrs):
 q=int(e['quarter']);h=byq[q]
 rows.append({'cycle':int(e['cycle']),'quarter':q,'season':h['season'],'module':h['module'],'channel':h['channel'],'depth_ppm':d,'internal_error_ppm':abs(d/s),'coverage_fraction':float(e['coverage_fraction']),'CROWDSAP':h['CROWDSAP'],'FLFRCSAP':h['FLFRCSAP']})
write_csv('event_depths.csv',rows)
def constant_test(rr):
 y=np.array([r['depth_ppm'] for r in rr]);e=np.array([r['internal_error_ppm'] for r in rr]);w=1/e**2;m=np.sum(y*w)/sum(w);stat=float(sum(((y-m)/e)**2));df=len(y)-1
 return {'quarters':[r['quarter'] for r in rr],'n':len(rr),'weighted_mean_ppm':float(m),'mean_internal_error_ppm':float(1/np.sqrt(sum(w))),'chi2':stat,'dof':df,'nominal_p':float(chi2.sf(stat,df)) if df else None,'error_multiplier_for_reduced_chi2_one':float(np.sqrt(stat/df)) if df else None}
summary={'assumptions':'Fixed selected ephemeris; independent Gaussian errors equal to internal depth/SNR; local baseline uncertainty, correlated noise, selection and partial-transit morphology unmodeled. P-values are nominal consistency diagnostics, not planet probabilities.'}
summary['six_events']=constant_test(rows);high=[r for r in rows if r['coverage_fraction']>=.85];summary['coverage_ge_85_percent']=constant_test(high);summary['coverage_ge_95_percent']=constant_test([r for r in rows if r['coverage_fraction']>=.95])
groups=[]
for mod in [17,19,7]:
 rr=[r for r in rows if r['module']==mod];a=constant_test(rr);groups.append({'module':mod,'channel':rr[0]['channel'],**a})
summary['module_groups']=groups
within=sum(x['chi2'] for x in groups);delta=summary['six_events']['chi2']-within
summary['exploratory_between_modules']={'delta_chi2':delta,'dof':2,'nominal_p':float(chi2.sf(delta,2)),'within_group_chi2':within,'within_group_dof':3,'caution':'Grouping inspected after seeing depths; only two events per module, confounded with epoch, gaps and partial coverage. Not causal attribution.'}
r14=next(r for r in high if r['quarter']==14);others=constant_test([r for r in high if r['quarter']!=14]);summary['q14_vs_other_high_coverage']={'other_mean_ppm':others['weighted_mean_ppm'],'other_mean_error_ppm':others['mean_internal_error_ppm'],'z_independent_white_noise':float((others['weighted_mean_ppm']-r14['depth_ppm'])/np.hypot(others['mean_internal_error_ppm'],r14['internal_error_ppm'])),'z_vs_fixed_75_17_ignoring_reference_error':float((75.17032342807539-r14['depth_ppm'])/r14['internal_error_ppm'])}
summary['zero_of_eight_binomial']={'n':8,'successes':0,'upper_95_one_sided':1-.05**(1/8),'upper_95_two_sided_equal_tail':1-.025**(1/8),'scope':'Hypothetical independent Bernoulli trials with common rate. Not validated for these shuffles; not a calibrated FAP or physical planet probability.'}
v=json.loads((root/'data/null_controls/vetting.json').read_text());selected=[x for x in v if 150<=x['period_days']<=300 and x['snr']>=10.028094622665412-1e-7];write_csv('high_snr_alerts.csv',selected)
sh=[x for x in selected if x['kind']=='shuffled'];counts=Counter(flag for x in sh for flag in x['vetting_flags'].split('|') if flag)
summary['high_snr_alerts']={'threshold':10.028094622665412,'all_selected':len(selected),'with_flags':sum(bool(x['vetting_flags']) for x in selected),'without_flags':sum(not x['vetting_flags'] for x in selected),'shuffled_selected':len(sh),'shuffled_flag_counts':dict(counts),'shuffled_only_duration_boundary':sum(x['vetting_flags']=='duration-at-boundary' for x in sh),'caution':'Detections in the same light curve are dependent; nominal and shuffled populations are not independent and this tally is not a reliability estimate.'}
(out/'summary.json').write_text(json.dumps(summary,indent=2))
fig,axs=plt.subplots(1,2,figsize=(12,4.8),layout='constrained');colors={17:'#2274a5',19:'#27916b',7:'#b65b30'}
for i,r in enumerate(rows):
 axs[0].errorbar(i,r['depth_ppm'],yerr=r['internal_error_ppm'],fmt='o' if r['coverage_fraction']>=.85 else 's',mfc=colors[r['module']] if r['coverage_fraction']>=.85 else 'white',mec=colors[r['module']],ecolor=colors[r['module']],capsize=4)
for i,g in enumerate(groups):axs[1].errorbar(i,g['weighted_mean_ppm'],yerr=g['mean_internal_error_ppm'],fmt='o',color=colors[g['module']],capsize=5)
axs[0].set_xticks(range(len(rows)),[f'Q{r["quarter"]}\n{r["module"]}/{r["channel"]}' for r in rows]);axs[1].set_xticks(range(3),[f'{g["module"]}/{g["channel"]}\n'+', '.join('Q'+str(q) for q in g['quarters']) for g in groups]);axs[0].set_title('Eventos: cuadrados huecos = cobertura <85 %');axs[1].set_title('Medias ponderadas por módulo/canal')
for ax in axs:
 ax.axhline(summary['six_events']['weighted_mean_ppm'],ls='--',lw=1,color='gray',label='Media local de seis eventos');ax.axhline(0,lw=.5,color='black');ax.set_ylim(-100,125);ax.set_ylabel('Profundidad local (ppm)');ax.grid(axis='y',alpha=.2);ax.yaxis.set_major_formatter(FuncFormatter(lambda x,p:f'{x:g}'.replace('.',',')))
axs[0].legend(fontsize=8);fig.suptitle('KIC 5610665 · profundidades y agrupación por detector\nBarras internas bajo ruido blanco; no incluyen incertidumbre completa de línea base')
fig.savefig(root/'figures/seasonal_depths.png',dpi=150);plt.close(fig)
print(json.dumps(summary,indent=2))
