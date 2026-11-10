"""Descarga las imágenes de fondo del panel desde los archivos oficiales de la NASA,
las reduce para la web y las guarda en local_panel/static/img con sus créditos.

Las imágenes de la NASA no tienen derechos de autor en EE. UU.; se citan los créditos
y no se usa ningún logotipo ni se sugiere respaldo de la NASA."""
from io import BytesIO
import json
from pathlib import Path
import urllib.request

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / 'local_panel/static/img'
IMAGES = {
    'kepler-field.jpg': dict(
        url='https://assets.science.nasa.gov/content/dam/science/psd/photojournal/pia/pia11/pia11984/PIA11984.jpg',
        title="Campo de visión completo de Kepler (PIA11984)",
        credit='NASA/Ames/JPL-Caltech',
        source='https://science.nasa.gov/photojournal/keplers-diamond-mine-of-stars'),
    'tess-south.jpg': dict(
        url='https://svs.gsfc.nasa.gov/vis/a010000/a013200/a013285/TESS_south_hires_azeq_no_labels_4K.jpg',
        title='Mosaico del cielo austral de TESS, primer año',
        credit='NASA/MIT/TESS and Ethan Kruse (USRA)',
        source='https://svs.gsfc.nasa.gov/13285/'),
}
MAX_SIDE = 1800


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Sakanet-panel/1.0'})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    credits = {}
    for name, info in IMAGES.items():
        print(f'Descargando {info["title"]}…', flush=True)
        image = Image.open(BytesIO(fetch(info['url']))).convert('RGB')
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        image.save(OUT / name, 'JPEG', quality=82, optimize=True, progressive=True)
        credits[name] = {key: info[key] for key in ('title', 'credit', 'source')}
        print(f'  guardada {name}: {image.size[0]}x{image.size[1]}, {(OUT / name).stat().st_size // 1024} KB', flush=True)
    (OUT / 'creditos.json').write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Listo. Recarga el panel (Ctrl+F5).')


if __name__ == '__main__':
    main()
