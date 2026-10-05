import urllib.request,json,hashlib
from pathlib import Path
out={}
for name,tag in [('python','3.13-slim'),('postgres','16'),('node','24-alpine')]:
 with urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/'+name+':pull', timeout=45) as r:token=json.load(r)['token']
 req=urllib.request.Request(f'https://registry-1.docker.io/v2/library/{name}/manifests/{tag}',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json'})
 with urllib.request.urlopen(req,timeout=45) as r:
  data=r.read();digest=r.headers['Docker-Content-Digest'];assert digest=='sha256:'+hashlib.sha256(data).hexdigest()
 out[name]={'tag':tag,'digest':digest,'index':json.loads(data)}
 print(name,digest,flush=True)
Path(__file__).with_name('resolved-image-pins.json').write_text(json.dumps(out,indent=2)+'\n')
