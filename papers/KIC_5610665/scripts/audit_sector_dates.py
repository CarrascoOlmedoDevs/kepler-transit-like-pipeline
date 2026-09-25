"""Compare nominal event centers with archived TESS sector calendar bounds.
Usage: python audit_sector_dates.py SECTOR_DATES_HTML
Download HTML from https://heasarc.gsfc.nasa.gov/docs/tess/sector.html;
sector IDs are preserved in data/catalog_checks/tess_sectors.json.
This is not a per-cadence coverage test or an ephemeris uncertainty analysis.
"""
import sys,json,csv,datetime,re
from pathlib import Path
from io import StringIO
import pandas as pd
from astropy.time import Time
root=Path(__file__).resolve().parents[1];out=root/'data/catalog_checks'
sectors={int(x['sector']) for x in json.loads((out/'tess_sectors.json').read_text())['results']}
rows=[]
for table in pd.read_html(StringIO(Path(sys.argv[1]).read_text())):
 for _,row in table.iterrows():
  match=re.fullmatch('s([0-9]+)',str(row['Sector']))
  if not match or int(match[1]) not in sectors:continue
  dates=[datetime.datetime.strptime(str(row[k]).replace('\xa0',' '),'%Y %b %d') for k in ['Start','Stop']]
  jd=[Time(x,scale='utc').tdb.jd for x in dates];n=round((sum(jd)/2-(2454833+260.54511369116005))/215.39750429110552);pred=2454833+260.54511369116005+n*215.39750429110552
  rows.append({'sector':int(match[1]),'start_utc_date':dates[0].date().isoformat(),'stop_utc_date':dates[1].date().isoformat(),'nearest_cycle':n,'nominal_center_bjd_tdb':pred,'center_inside_date_interval':bool(jd[0]<=pred<=jd[1]),'days_before_start_if_positive':jd[0]-pred,'days_after_stop_if_positive':pred-jd[1]})
with (out/'tess_sector_windows.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(sorted(rows,key=lambda x:x['sector']))
