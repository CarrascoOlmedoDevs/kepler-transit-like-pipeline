"""Download archived observations. Writes mismatched products separately and fails."""
import hashlib, io, json, sys, urllib.request, zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]) if len(sys.argv)>1 else root/'raw'
out.mkdir(parents=True,exist_ok=True)
for item in json.loads((root/'data/download_manifest.json').read_text()):
    dst=out/item['filename']
    if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest()==item['sha256']:
        print('Cached',dst.name);continue
    with urllib.request.urlopen(item['url'],timeout=180) as response:
        data=response.read()
    if item.get('format')=='zip_with_fits':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names=[x for x in archive.namelist() if x.endswith('.fits')]
            if len(names)!=1:raise RuntimeError('Unexpected TESScut members')
            data=archive.read(names[0])
    if hashlib.sha256(data).hexdigest()!=item['sha256']:
        dst.with_suffix(dst.suffix+'.changed').write_bytes(data)
        raise RuntimeError(f'Archive bytes changed: {dst.name}; inspect headers/data before reuse. TESScut may regenerate headers.')
    dst.write_bytes(data)
    print('Verified',dst.name)
