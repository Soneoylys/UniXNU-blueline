#!/usr/bin/env python3
"""Compile board GPU policy with the resolved XNU C recipe; no hardware access."""
from pathlib import Path
import argparse,importlib.util,json,subprocess
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--tree',type=Path,required=True)
p.add_argument('--components',type=Path,required=True)
p.add_argument('--soc',type=Path,required=True)
a=p.parse_args();tree=a.tree.resolve();components=a.components.resolve();soc=a.soc.resolve()
builder=soc/'kits/Sdm845Gpu/build-native.py'
spec=importlib.util.spec_from_file_location('sdm845_gpu_build',builder)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
source=Path(__file__).resolve().parent/'BluelineGpuPolicy.c'
out=tree/'build/blueline-gpu-policy';out.mkdir(parents=True,exist_ok=True)
recipe_path=tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json'
recipe=json.loads(recipe_path.read_text().strip().rstrip(','))
obj=out/'BluelineGpuPolicy.o'
cmd=module.command(recipe,source,obj,[source.parent,soc/'kits/Sdm845Gpu',components/'kits/AdrenoFirmware'])
run=subprocess.run(cmd,cwd=recipe['directory'],capture_output=True,text=True)
(out/'compile.log').write_text(run.stdout+run.stderr,encoding='utf-8')
if run.returncode:raise SystemExit(run.stderr[-4000:])
report=dict(source=str(source),source_sha256=module.sha(source),object=module.audit_object(obj),command=cmd,
            recipe_sha256=module.sha(recipe_path),hardware_qualified=False)
(out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('PASS: board GPU policy ARM64/iOS/-Werror object; no hardware qualification')
