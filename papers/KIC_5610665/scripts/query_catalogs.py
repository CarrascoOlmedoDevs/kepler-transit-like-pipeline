import subprocess,urllib.parse,json,csv,hashlib,datetime
from pathlib import Path
out=Path(__file__).resolve().parents[1]/'data/catalog_checks';out.mkdir(exist_ok=True)
queries={'cumulative':'select * from cumulative where kepid=5610665','tce_dr25':'select * from q1_q17_dr25_tce where kepid=5610665','stellar':'select * from keplerstellar where kepid=5610665','confirmed_names':'select * from keplernames where kepid=5610665'}
log=[]
for name,q in queries.items():
 url='https://exoplanetarchive.ipac.caltech.edu/TAP/sync?'+urllib.parse.urlencode({'query':q,'format':'csv'})
 p=out/(name+'.csv');r=subprocess.run(['curl','--fail','-L','--max-time','30','-sS',url,'-o',str(p)],capture_output=True)
 valid=r.returncode==0 and p.exists() and 'kepid' in next(csv.reader(p.read_text().splitlines()),[])
 rows=list(csv.DictReader(p.read_text().splitlines())) if valid else None
 info={'name':name,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'query':q,'url':url,'success':valid,'rows':len(rows) if rows is not None else None,'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None,'error':r.stderr.decode()};log.append(info);print(info,flush=True)
 (out/'query_log.json').write_text(json.dumps(log,indent=2))
