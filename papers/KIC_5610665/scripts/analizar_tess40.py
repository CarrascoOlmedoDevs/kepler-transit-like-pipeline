"""Exploratory aperture photometry and masked detrending; not planet validation.
Usage: python analizar_tess40.py PATH_TO_FITS [OUTPUT_DIRECTORY]
Dependencies: numpy scipy astropy matplotlib.
"""
import sys,json,csv
from pathlib import Path
import numpy as np
from scipy.interpolate import LSQUnivariateSpline
from astropy.io import fits
from astropy.wcs import WCS
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

src=Path(sys.argv[1]); out=Path(sys.argv[2]) if len(sys.argv)>2 else Path('.')
out.mkdir(parents=True,exist_ok=True)
h=fits.open(src);d=h[1].data
T=np.asarray(d['TIME'],float); F=np.asarray(d['FLUX'],float); E=np.asarray(d['FLUX_ERR'],float)
Q=d['QUALITY']; ok=(Q==0)&np.isfinite(T)&np.all(np.isfinite(F),axis=(1,2))
t=T[ok];f=F[ok];err=E[ok]
P=215.39750429110552;T0=2454833+260.54511369116005
center=T0+20*P-2457000; duration=13.4316/24; protect=.65
pos=WCS(h[2].header).all_world2pix([[288.910601,40.800590]],0)[0]
yy,xx=np.indices(f.shape[1:]);r=np.hypot(xx-pos[0],yy-pos[1]);med=np.median(f,axis=0)
# Use the faintest quartile at least 2.5 pixels from the target; this is
# an empirical sky estimate and can contain unresolved stellar light.
bmask=(med<=np.percentile(med,25))&(r>2.5)
bg=np.median(f[:,bmask],axis=1)
masks={'central_2x2':(xx>=3)&(xx<=4)&(yy>=3)&(yy<=4),'radio_1p5':r<=1.5,'radio_2':r<=2}

def mad(a):
 return 1.4826*np.median(abs(a-np.median(a)))

def polytrend(x,y,c,width,degree,exclude=.65):
 local=abs(x-c)<width; use=local&(abs(x-c)>exclude)
 for _ in range(5):
  coef=np.polyfit(x[use]-c,y[use],degree)
  base=np.polyval(coef,x-c);res=y-base; s=mad(res[use]); new=local&(abs(x-c)>exclude)&(abs(res)<5*s)
  if np.array_equal(new,use):break
  use=new
 return base,use

def measure(x,y,c,width,degree):
 base,use=polytrend(x,y,c,width,degree)
 inside=abs(x-c)<duration/2
 if inside.sum()<50 or sum(use&(x<c))<80 or sum(use&(x>c))<80:return None
 residual=y/base-1
 return float(-np.mean(residual[inside])*1e6),residual,base,use

results=[];curves={};controls={}
for name,mask in masks.items():
 raw=f[:,mask].sum(axis=1); flux=raw-mask.sum()*bg
 norm=flux/np.median(flux); inside=abs(t-center)<duration/2
 # Full sector trend: cubic spline, knots every 3 d, segmented at gaps >0.5 d.
 trend=np.full(len(t),np.nan)
 edges=np.r_[0,np.where(np.diff(t)>.5)[0]+1,len(t)]
 for a,b in zip(edges[:-1],edges[1:]):
  ts=t[a:b];ys=norm[a:b];fit=abs(ts-center)>protect
  knots=np.arange(ts.min()+3,ts.max()-1,3)
  for _ in range(4):
   model=LSQUnivariateSpline(ts[fit],ys[fit],knots,k=3)
   resid=ys-model(ts);fit=(abs(ts-center)>protect)&(abs(resid)<5*mad(resid[fit]))
  trend[a:b]=model(ts)
 full=norm/trend-1
 for width,degree in [(2,1),(2,2),(3,1),(3,2)]:
  ans=measure(t,norm,center,width,degree)
  depth,res,base,fit=ans
  # Matched null windows throughout sector; avoid real protected window and
  # gaps. Samples overlap, so dispersion is descriptive, not a calibrated FAP.
  null=[]
  for c in np.arange(t.min()+width,t.max()-width,.7):
   if abs(c-center)<width+protect:continue
   if np.max(np.diff(t[abs(t-c)<width]))>.1:continue
   val=measure(t,norm,c,width,degree)
   if val is not None:null.append(val[0])
  emp=mad(np.array(null)) if len(null)>2 else np.nan
  rms=mad(res[fit])*1e6
  row=dict(apertura=name,pixeles=int(mask.sum()),semiventana_d=width,grado=degree,profundidad_ppm=depth,dispersion_control_ppm=float(emp),numero_controles=len(null),ruido_cadencia_ppm=float(rms),n_dentro=int(inside.sum()))
  results.append(row)
  if width==2 and degree==1:
   curves[name]=(raw,flux,norm,trend,full,res,base,fit)
   controls[name]=null

primary='central_2x2';raw,flux,norm,trend,full,res,base,fit=curves[primary]
with open(out/'curva_corregida.csv','w') as fp:
 writer=csv.writer(fp);writer.writerow(['BTJD_TDB','BJD_TDB','flujo_apertura_e_s','fondo_por_pixel_e_s','flujo_sin_fondo_e_s','normalizado','tendencia_spline_3d','residuo_spline_ppm','tendencia_local_lineal','residuo_local_ppm','ventana_prevista'])
 for i in range(len(t)):
  writer.writerow([t[i],t[i]+2457000,raw[i],bg[i],flux[i],norm[i],trend[i],full[i]*1e6,base[i] if abs(t[i]-center)<2 else '',res[i]*1e6 if abs(t[i]-center)<2 else '',int(abs(t[i]-center)<duration/2)])
with open(out/'sensibilidad_aperturas.csv','w') as fp:
 writer=csv.DictWriter(fp,fieldnames=results[0].keys());writer.writeheader();writer.writerows(results)
summary=dict(centro_BTJD_TDB=center,duracion_h=duration*24,mascara_ajuste_h=protect*48,calidad_0=len(t),muestras_originales=len(T),posicion_pixel=pos.tolist(),pixeles_fondo=int(bmask.sum()),resultados=results)
(out/'resumen.json').write_text(json.dumps(summary,indent=2))

fig,ax=plt.subplots(3,1,figsize=(12,10),layout='constrained')
ax[0].plot(t,raw/np.median(raw),'.',ms=1.6,color='#8299ad',label='Apertura sin corregir')
ax[0].plot(t,norm,'.',ms=1.6,color='#156ba5',label='Tras sustraer fondo')
ax[0].plot(t,trend,color='#df8821',lw=1.5,label='Tendencia spline (nudos cada 3 días)')
ax[0].set(ylabel='Flujo relativo',title='KIC 5610665 · TESS sector 40 · apertura central 2 × 2')
ax[0].legend(fontsize=9,ncol=3)
ax[1].plot(t,full*1e6,'.',ms=2,color='#156ba5');ax[1].axhline(0,color='gray',lw=.8)
ax[1].set(ylabel='Residuo (ppm)',xlabel='BTJD = BJD − 2457000 (TDB)',title='Sector tras corregir fondo y tendencia temporal')
for a in ax[:2]:a.axvspan(center-duration/2,center+duration/2,color='#df8821',alpha=.22)
for name,color in zip(masks,['#156ba5','#d76b29','#4b9861']):
 rr=curves[name][5]*1e6
 for low in np.arange(-2,2,1/12):
  m=(t-center>=low)&(t-center<low+1/12)
  if sum(m)>3:ax[2].plot((low+1/24)*24,np.mean(rr[m]),'o',ms=4,color=color,label=name if low==-2 else None)
ax[2].axvspan(-duration*12,duration*12,color='#df8821',alpha=.18,label='Ventana prevista: 13,43 h')
ax[2].plot([-duration*12,-duration*12,duration*12,duration*12],[0,-75,-75,0],color='black',lw=1.5,label='Referencia de 75 ppm, sin ajustar')
ax[2].axhline(0,color='gray',lw=.8);ax[2].set(xlabel='Horas respecto al centro previsto',ylabel='Residuo (ppm)',title='Ajuste local lineal ±2 días · puntos agrupados cada 2 horas')
ax[2].legend(fontsize=8,ncol=2)
fig.savefig(out/'TESS40_tendencia_corregida.png',dpi=150)
plt.close(fig)
fig,ax=plt.subplots(figsize=(6,5),layout='constrained');ax.imshow(med,origin='lower',cmap='magma');ax.contour(masks[primary],levels=[.5],colors=['cyan']);ax.scatter(xx[bmask],yy[bmask],marker='s',facecolors='none',edgecolors='lime',label='Píxeles de fondo');ax.plot(*pos,'+',color='white',ms=12,label='Posición KIC');ax.legend(fontsize=8);ax.set(title='TESS sector 40: apertura y fondo',xlabel='Columna del recorte',ylabel='Fila del recorte');fig.savefig(out/'apertura_y_fondo.png',dpi=150);plt.close(fig)
print(json.dumps(summary,indent=2))
