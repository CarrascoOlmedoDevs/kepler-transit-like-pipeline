import glob, warnings, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from astropy.io import fits

T0=260.54511369116005; P=215.39750429110552; duration=13.4316/24
cases=[(0,3),(2,7),(3,10),(4,12),(5,14),(6,16)]
paths=glob.glob(str(Path(sys.argv[1])/'*_lpd-targ.fits.gz'))
fig,axes=plt.subplots(6,3,figsize=(13.2,19.2),facecolor='#0d1424',constrained_layout=True)
for row,(cycle,quarter) in enumerate(cases):
    path=next(x for x in paths if fits.getheader(x)['QUARTER']==quarter)
    with fits.open(path) as fitsfile:
        tab=fitsfile[1].data
        times=np.asarray(tab['TIME'],float)
        good=(tab['QUALITY']==0)&np.isfinite(times)
        event=T0+cycle*P
        inside=good&(abs(times-event)<duration/2)
        outside=good&(abs(times-event)>0.6)&(abs(times-event)<2)
        data=np.asarray(tab['FLUX'],float)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',RuntimeWarning)
            outside_img=np.nanmedian(data[outside],axis=0)
            inside_img=np.nanmedian(data[inside],axis=0)
        delta=outside_img-inside_img
        aperture=fitsfile[2].data==3
    positive=np.concatenate((outside_img[np.isfinite(outside_img)],inside_img[np.isfinite(inside_img)]))
    vmin,vmax=np.percentile(positive,[5,99.8])
    for col,(img,title) in enumerate(((outside_img,'Fuera'),(inside_img,'Durante'),(delta,'Diferencia: fuera − durante'))):
        ax=axes[row,col]
        ax.set_facecolor('#121d31')
        if col<2:
            ax.imshow(img,cmap='magma',vmin=vmin,vmax=vmax,origin='lower',interpolation='nearest')
        else:
            scale=np.nanpercentile(abs(delta),95)
            scale=max(scale,1.0)
            ax.imshow(delta,cmap='coolwarm',vmin=-scale,vmax=scale,origin='lower',interpolation='nearest')
        ax.contour(aperture.astype(float),levels=[0.5],colors=['#6ce2ed'],linewidths=1.5,origin='lower')
        ax.set_xticks([]);ax.set_yticks([])
        for spine in ax.spines.values():spine.set_color('#60718b')
        ax.set_title(title,color='#f0f4ff',fontsize=12,pad=8)
        if col==0:
            ax.set_ylabel(f'Q{quarter} · BKJD {event:.3f}\n{sum(inside)} puntos dentro',color='#f0f4ff',fontsize=11,labelpad=12)
fig.suptitle('KIC 5610665 · imágenes de píxeles en seis ventanas previstas',color='white',fontsize=19)
out=sys.argv[2]
fig.savefig(out,dpi=165,bbox_inches='tight',facecolor=fig.get_facecolor())
print(out)
