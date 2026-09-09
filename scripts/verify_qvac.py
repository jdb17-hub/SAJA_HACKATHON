"""Smoke real de QVAC. No escribe inventario ni usa sustitutos de inferencia.

Para verificar desconectado, preparar modelos, desconectar manualmente la red
 y ejecutar el mismo comando. El reporte no afirma haber desconectado la red.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.qvac_engine import MotorQVAC
from src.extract import extraer
from src import nlquery

parser=argparse.ArgumentParser()
parser.add_argument('--audio',type=Path)
args=parser.parse_args()
report={'timestamp':datetime.now(timezone.utc).isoformat(),'offline_verified':False,'cases':[]}
m=MotorQVAC()
try:
    m.iniciar()
    if not m.estado.listo:
        raise RuntimeError(m.estado.error)
    report['model']=m.estado.llm_model
    for text in [
        'Estoy en Hospital DemoCare Pacific, en Panamá. Tienen dos resonadores y un tomógrafo. Uno de los resonadores parece de unos ocho años.',
        'Estoy en Hospital DemoCare, tienen dos resonadores.',
        'Estoy en el Hospital Nuevo Horizonte, tienen un tomógrafo.']:
        b=extraer(text,m,estricto=True)
        report['cases'].append({'input':text,'result':b.model_dump(mode='json')})
    primary=report['cases'][0]['result']['items']
    assert sum(e['quantity'] or 0 for e in primary)==3
    assert sum(e['quantity'] or 0 for e in primary if e['modality']=='MR')==2
    assert sum(e['quantity'] or 0 for e in primary if e['age_years']==8)==1
    assert report['cases'][1]['result']['customer']=='Unknown'
    assert report['cases'][2]['result']['customer']!='Unknown'
    report['query']=nlquery.interpretar('clientes en Brasil con más de cinco resonadores de más de siete años',m)
    assert (report['query']['pais'],report['query']['cantidad_min'],report['query']['edad_min'])==('Brazil',6,8)
    if args.audio:
        report['audio_input']=str(args.audio)
        report['transcription']=m.transcribir(args.audio)
        assert report['transcription'].strip()
    report['passed']=True
except Exception as exc:
    report['passed']=False
    report['error']=f'{type(exc).__name__}: {exc}'
finally:
    report['inferences']=m.estado.inferencias
    m.cerrar()
    path=Path(__file__).resolve().parents[1]/'reports'/'qvac-smoke.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'error':report.get('error'),'report':str(path)}))
sys.exit(0 if report['passed'] else 1)
