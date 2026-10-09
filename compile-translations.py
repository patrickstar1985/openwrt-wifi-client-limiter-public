#!/usr/bin/env python3
"""Compile native LuCI catalogs off-router. Requires LuCI host tool po2lmo."""
import json, os, shutil, subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
compiler=os.environ.get('PO2LMO') or shutil.which('po2lmo')
if not compiler:raise SystemExit('po2lmo missing: set PO2LMO to the LuCI host tool path')
target=root/'files/usr/lib/lua/luci/i18n';target.mkdir(parents=True,exist_ok=True)
for lang in json.loads((root/'languages.json').read_text()):
 if lang['po']=='en':continue # English source strings are the native fallback.
 src=root/'po'/lang['po']/'wifi-client-limiter.po'
 dst=target/('wifi-client-limiter.'+lang['runtime']+'.lmo')
 subprocess.run([compiler,str(src),str(dst)],check=True)
 if not dst.is_file() or not dst.stat().st_size:raise SystemExit('Missing compiled catalog: '+str(dst))
 print(lang['runtime']+' OK')
