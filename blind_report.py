"""Post-scan reports only. Frozen matching functions and observations are unchanged."""
from datetime import datetime
from pathlib import Path
import json
import numpy as np
import pandas as pd
import evaluate_blind as ev
from blind_resume import ISSUE, verify_preserved
from kepler_detector.blind_metrics import summarize, rate
from kepler_detector.mast import load_curve
from kepler_detector.utils import atomic_csv, file_hash
from local_panel.jobs import read, write


def observed_events(times, period, epoch, duration_hours):
    """Coverage proxy on cached timestamps, never a fitted/detected event count."""
    times = np.asarray(times)
    times = times[np.isfinite(times)]
    if not np.isfinite(period) or period <= 0 or not np.isfinite(epoch):
        return None
    half = max(duration_hours / 48 if np.isfinite(duration_hours) and duration_hours > 0 else 0, 1765.5/86400/2)
    indices = np.rint((times - epoch) / period).astype(np.int64)
    return int(len(np.unique(indices[np.abs(times - (epoch + indices * period)) <= half])))


def binned(frame, column, ranges):
    rows = []
    for label, mask in ranges(frame[column]):
        part = frame[mask]
        metric = rate(part.recovered_fundamental.sum(), len(part))
        rows.append(dict(bin=label, N=metric['denominator'], recovered_fundamental=metric['n'],
                         recovery_percent=metric['percent']))
    return pd.DataFrame(rows)


def period_bins(x):
    return [('<3',x<3),('3–<10',(x>=3)&(x<10)),('10–<30',(x>=10)&(x<30)),
            ('30–<100',(x>=30)&(x<100)),('100–300',(x>=100)&(x<=300)),('>300',x>300)]


def depth_bins(x):
    return [('<200',x<200),('200–<500',(x>=200)&(x<500)),('500–<1000',(x>=500)&(x<1000)),
            ('1000–3000',(x>=1000)&(x<=3000)),('>3000',x>3000)]


def count_bins(x):
    return [('<3',x<3),('3–4',(x>=3)&(x<=4)),('5–10',(x>=5)&(x<=10)),
            ('11–30',(x>=11)&(x<=30)),('31–100',(x>=31)&(x<=100)),('>100',x>100)]


def diagnostic(row):
    if row.recovered_fundamental and not row.recovered_family:
        return 'candidate family grouping', 'Fundamental raw assignment exists but no family assignment under frozen one-to-one matching.'
    if row.recovered_harmonic:
        return 'harmonic only', 'Only a harmonic was assigned under the frozen period and phase criterion.'
    if row.raw_assignment_conflict:
        return 'unknown', 'Eligible edge exists but loses frozen one-to-one raw assignment.'
    return 'unknown', 'No assigned fundamental or harmonic in saved raw detections; peak/SNR/masking cause is not established by saved telemetry.'


def plot_bins(out, name, table, xlabel, note=''):
    from kepler_detector.plotting import plt, save
    fig, ax = plt.subplots(figsize=(9,5))
    if int(table.N.sum()) == 0:
        ax.set_axis_off()
        ax.text(.5,.58,'Analysis unavailable',ha='center',va='center',fontsize=18,transform=ax.transAxes)
        ax.text(.5,.43,'No reference values available (N = 0).\nNo recovery percentage can be estimated.',
                ha='center',va='center',fontsize=12,transform=ax.transAxes)
        ax.set_title(xlabel)
        if note:
            fig.text(.5,.05,note,ha='center',fontsize=10)
        save(fig,out/name)
        return
    x = np.arange(len(table))
    ax.bar(x, table.recovery_percent.fillna(0), color='#376b9b')
    ax.set(xticks=x, xticklabels=table['bin'], ylim=(0,115), ylabel='Fundamental raw recovery (%)', xlabel=xlabel)
    for i,row in table.iterrows():
        text = f"{int(row.recovered_fundamental)}/{int(row.N)}" if row.N else 'N=0 (n/a)'
        ax.text(i, (row.recovery_percent if pd.notna(row.recovery_percent) else 0)+3, text, ha='center', fontsize=9)
    ax.set_title('Pipeline 2.0 · descriptive bins; counts shown (no completeness correction)', fontsize=10)
    if note:
        fig.text(.5,.01,note,ha='center',fontsize=8)
    save(fig,out/name)


def compact(s):
    def r(m):
        return f"{m['n']}/{m['denominator']} ({m['percent']:.1f}%)" if m['percent'] is not None else 'n/a'
    rank=s['rank_fundamental']; c=s['control']; multi=s['multiplanet']
    return '\n'.join(['BLIND END-TO-END EVALUATION  PIPELINE 2.0','==========================================',
        f"Stars:                         {s['completed_stars']}/92",'Known KOI candidates:         123','',
        f"Fundamental raw:              {r(s['fundamental_raw'])}",
        f"Fundamental families:         {r(s['fundamental_family'])}",
        f"Fundamental + harmonic:       {r(s['fundamental_plus_harmonic_raw'])}",'',
        *[f"Top {k} (fundamental):          {r(s['fundamental_top'][str(k)])}" for k in (1,3,5,10)],'',
        f"Stars >=1 recovered:          {r(s['per_star']['any_recovered'])}",'', 'Multiplanet systems:',
        *[f"{key}: {multi[key]['n']}/{multi[key]['denominator']}" for key in ('all','partial','none')],'',
        f"Median rank:                  {rank['median']}",f"Mean rank:                    {rank['mean']}",
        f"MRR (all 123 KOI):            {rank['mrr_all_koi']:.3f}",'',
        f"RF positive | BLS fundamental: {r(s['rf_positive_given_bls_fundamental'])}",
        f"RF positive | BLS any:         {r(s['rf_positive_given_bls_any'])}",'',
        f"Median families/star:         {s['median_families_per_star']}",
        f"Median unmatched/star:        {s['median_unmatched_families_per_star']}",
        f"Median unmatched above KOI:   {s['median_unmatched_above_recovered']}",'',
        f"Runtime total (active wall):  {s['runtime_total_seconds']:.1f} s",
        f"Mean runtime/star:            {s['runtime_mean_per_star_seconds']:.1f} s",'',
        'Known Pipeline 2.0 issues:', '- possible resonance/harmonic family collision','',
        'KIC 10854555 CONTROL (excluded):',f"period = {c['detected_period']}",f"known period = {c['known_period']}",
        f"period error (%) = {c['period_error_percent']}",f"rank = {c['rank']}",f"status = {c['status']}"])+'\n'


def generate(root):
    root=Path(root).resolve(); out=root/'results/blind_evaluation'
    frozen=ev.verify_frozen(root,out); verify_preserved(out)
    manifest=read(out/'evaluation_manifest.json')
    cohort=pd.read_csv(out/'cohort_koi.csv')
    states={int(k):ev.existing_attempt(out,int(k)) for k in cohort.kepid.unique()}
    if any(not s or s['status'] not in ('complete','failed') for s in states.values()):
        raise ValueError('Official cohort still incomplete; no final report written')
    koi,stars=ev.result_tables(out,cohort,states)
    catalog=pd.read_csv(root/'data/koi_dr25.csv')
    depth_available='koi_depth' in catalog.columns
    if not depth_available:
        catalog['koi_depth']=np.nan
    depths=catalog[['kepid','kepoi_name','koi_depth']].drop_duplicates(['kepid','kepoi_name'])
    koi=koi.merge(depths,on=['kepid','kepoi_name'],how='left',validate='one_to_one')
    koi=koi.merge(stars[['kepid','number_unmatched_families']],on='kepid',how='left',validate='many_to_one')
    koi['families_ranked_above_true_signal']=koi.number_of_families_ranked_above_true_signal
    koi['number_observed_koi_transit_windows']=np.nan
    for kic,group in koi.groupby('kepid'):
        curve=load_curve(out/f'blind_workspace/cache/lightcurves/KIC_{int(kic):08d}.npz')
        for idx,row in group.iterrows():
            koi.loc[idx,'number_observed_koi_transit_windows']=observed_events(curve.time,row.koi_period,row.koi_epoch,row.koi_duration)
    for idx,star in stars.iterrows():
        group=koi[koi.kepid==star.kepid]
        n=int(group.recovered_raw.sum())
        for key,value in dict(number_recovered=n,number_fundamental=int(group.recovered_fundamental.sum()),
            number_harmonic=int(group.recovered_harmonic.sum()),number_not_recovered=len(group)-n,
            all_recovered=n==len(group),partial_recovery=0<n<len(group),none_recovered=n==0,
            runtime_seconds=star.seconds).items():
            stars.loc[idx,key]=value
        for k in (1,3,5,10):
            stars.loc[idx,f'any_top_{k}']=bool((group['rank']<=k).any())
    for column in ('number_recovered','number_fundamental','number_harmonic','number_not_recovered'):
        stars[column]=stars[column].astype(int)
    for column in ('all_recovered','partial_recovery','none_recovered','any_top_1','any_top_3','any_top_5','any_top_10'):
        stars[column]=stars[column].astype(bool)
    started=datetime.fromisoformat(read(out/'timing.json')['started_at'])
    pause=datetime.fromisoformat(read(out/'pause_state.json')['paused_at'])
    sessions=[read(p) for p in (out/'resume_sessions').glob('*.json')]
    # Include tasks that completed through the panel before the controlled continuation.
    session_ids={k for s in sessions for k in s['completed']}
    previous_seconds=sum(s.get('seconds',0) for k,s in states.items()
        if k not in manifest['completed_before_resume'] and k not in session_ids)
    runtime=(pause-started).total_seconds()+previous_seconds+sum(s.get('official_wall_seconds',0) for s in sessions)
    summary=summarize(koi,stars,runtime)
    summary.update(harmonic_family=rate((koi.recovered_harmonic & koi.recovered_family).sum(),123),
        rf_positive_given_bls_any=rate((koi.recovered_raw & (koi.rf_prediction==1)).sum(),koi.recovered_raw.sum()),
        known_issues=[ISSUE], frozen_inputs_unchanged=True, original_22_outputs_unchanged=True,
        runtime_definition='Original active wall until user pause + sequential completed panel tasks before coordinator + coordinator official wall. Pauses and control excluded; aborted task CPU time not estimated.',
        ranking_definition='Top K fundamental family rank; MRR denominator all 123 KOI; unmatched/unassigned rank contributes zero. Combined metrics separate.',
        max_detections=5, top10_limitation='Default max_planets=5 retained; Top10 cannot exceed Top5.',
        unmatched_definition='No eligible period+phase edge to the frozen CANDIDATE reference set; not a physical false-positive label.')
    summary['elapsed_calendar_seconds_including_pauses']=(datetime.fromisoformat(ev.utc_now())-started).total_seconds()
    for k in (1,3,5,10):
        summary['per_star'][f'any_top_{k}']=rate(stars[f'any_top_{k}'].sum(),92)
    control_state=ev.existing_attempt(out,10854555)
    if not control_state or control_state['status'] not in ('complete','failed'):
        raise ValueError('Separate control not completed')
    ck,_=ev.result_tables_control(out,pd.read_csv(out/'control_reference.csv'),control_state)
    row=ck.iloc[0]
    nullable=lambda v: None if pd.isna(v) else float(v)
    summary['control']=dict(kepid=10854555,status='PASS' if row.recovered_fundamental else 'FAIL',
        detected_period=nullable(row.detected_period),known_period=float(row.koi_period),
        period_error_percent=nullable(row.period_error_percent),rank=nullable(row['rank']),seconds=control_state['seconds'])
    tables={
        'period':binned(koi,'koi_period',period_bins),
        'depth':binned(koi,'koi_depth',depth_bins),
        'transit_count':binned(koi,'number_observed_koi_transit_windows',count_bins)}
    for name,table in tables.items():
        atomic_csv(table,out/f'recovery_vs_{name}.csv')
        summary[f'recovery_vs_{name}']=json.loads(table.to_json(orient='records'))
    summary['missing_depth_count']=int(koi.koi_depth.isna().sum())
    summary['depth_analysis_status']='available' if depth_available else 'unavailable: frozen local catalog has no koi_depth column; no detector-derived substitute used'
    summary['transit_count_definition']=manifest['transit_count_method']
    misses=koi[(~koi.recovered_fundamental)|(~koi.recovered_family)].copy()
    reasons=[diagnostic(row) for _,row in misses.iterrows()]
    misses['diagnosis']=[r[0] for r in reasons]; misses['diagnosis_evidence']=[r[1] for r in reasons]
    misses['period']=misses.koi_period; misses['duration']=misses.koi_duration; misses['depth']=misses.koi_depth
    misses['loss_stage']=np.where(misses.recovered_raw,'family or fundamental-only metric','raw')
    atomic_csv(misses,out/'not_recovered.csv')
    atomic_csv(koi[koi.recovered_fundamental & (koi.rf_probability<.5)],out/'rf_false_negatives.csv')
    atomic_csv(koi,out/'koi_results.csv'); atomic_csv(stars,out/'star_results.csv')
    ev.charts(out,koi,stars,summary)
    plot_bins(out,'recovery_vs_period.png',tables['period'],'KOI period (days)')
    plot_bins(out,'recovery_vs_depth.png',tables['depth'],'KOI depth (ppm)',f"Missing depth excluded only here: {summary['missing_depth_count']}")
    plot_bins(out,'recovery_vs_transit_count.png',tables['transit_count'],'Observed KOI transit windows in cached timestamps','Coverage proxy before preprocessing; not a fitted number of transits')
    manifest.update(report_status='complete',finished_at=ev.utc_now(),preserved_outputs_verified=True,
                    reporting_code_hashes={name:file_hash(root/name) for name in ('blind_report.py','blind_resume.py')})
    archives=[out/'operational_retry_archive',root/'results/local_panel/blind/history']
    manifest['operational_archive_hashes']={str(p.relative_to(root)):file_hash(p)
        for base in archives for p in base.rglob('*') if p.is_file()}
    write(out/'evaluation_manifest.json',manifest)
    write(out/'summary.json',summary)
    text=compact(summary)+'\nADDITIONAL METRICS\n'+ev.detailed_summary(summary)
    hm=summary['harmonic_family']
    text+=f"\nHarmonic family recovery: {hm['n']}/{hm['denominator']} ({hm['percent']:.1f}%)\n"
    for name,table in tables.items():
        text+='\nRECOVERY BY '+name.upper()+'\n'+table.to_string(index=False)+'\n'
    text+='\nnot_recovered.csv includes raw misses, harmonic-only recoveries and raw-to-family losses.\nUnknown causes are not inferred as masking/low SNR without evidence.\n'+summary['runtime_definition']+'\n'
    text+='\nDepth analysis: '+summary['depth_analysis_status']+'\n'
    (out/'summary.txt').write_text(text,encoding='utf-8')
    print(compact(summary),flush=True)
    return summary


if __name__=='__main__':
    generate(Path(__file__).resolve().parent)
