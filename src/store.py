"""Visitas, revisiones e inventario vigente; adaptación del patrón de QVAC completo."""
from __future__ import annotations
import csv
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from .config import DATA_DIR, DB_PATH
from .schema import Borrador, Observacion, Modalidad, es_desconocido
from . import normalize as N


def pack(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def ahora():
    return datetime.now().isoformat(timespec='seconds')


def identidad(row):
    return pack([N.clave(row.get('customer','')), N.clave(row.get('city','')),
                 N.clave(N.normalizar_pais(row.get('country','')))])


def fecha_visita(valor):
    if not valor:
        return ''
    parsed = date.fromisoformat(str(valor)[:10])
    if parsed > date.today():
        raise ValueError('La fecha de visita no puede ser futura.')
    return parsed.isoformat()


def conectar():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    legacy = con.execute("SELECT 1 FROM sqlite_master WHERE name='observaciones'").fetchone()
    ready = con.execute("SELECT 1 FROM sqlite_master WHERE name='ib_meta'").fetchone()
    if legacy and not ready:
        backup = DB_PATH.with_name(DB_PATH.name + '.pre-v2.bak')
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as dest:
                con.backup(dest)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS ib_meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS ib_visits(
      id INTEGER PRIMARY KEY, original TEXT NOT NULL, payload TEXT NOT NULL,
      stage TEXT NOT NULL DEFAULT 'draft', version INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ib_revisions(
      id INTEGER PRIMARY KEY, visit_id INTEGER NOT NULL REFERENCES ib_visits(id),
      created_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ib_equipment(
      id INTEGER PRIMARY KEY, identity TEXT NOT NULL, modality TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL,
      visit_id INTEGER NOT NULL REFERENCES ib_visits(id));
    CREATE INDEX IF NOT EXISTS ib_current ON ib_equipment(identity,modality,active);
    CREATE TABLE IF NOT EXISTS ib_evidence(
      equipment_id INTEGER NOT NULL REFERENCES ib_equipment(id),
      visit_id INTEGER NOT NULL REFERENCES ib_visits(id),
      compatible INTEGER NOT NULL, confirms INTEGER NOT NULL,
      PRIMARY KEY(equipment_id,visit_id));
    ''')
    with con:
        con.execute('BEGIN IMMEDIATE')
        if legacy and not con.execute("SELECT 1 FROM ib_meta WHERE key='legacy_migrated'").fetchone():
            for row in con.execute('SELECT * FROM observaciones').fetchall():
                data = dict(row)
                data['quantity'] = data.get('quantity') or None
                data['age_years'] = data.get('age_years') or None
                data['notes'] = (data.get('notes') or '') + ' | Migrado: revisar duplicados históricos.'
                _importar(con, Observacion.model_validate(data))
            con.execute("INSERT INTO ib_meta VALUES('legacy_migrated','1')")
    return con


def _revision(con, vid, kind, payload):
    con.execute('INSERT INTO ib_revisions(visit_id,created_at,kind,payload) VALUES(?,?,?,?)',
                (vid,ahora(),kind,pack(payload)))


def _importar(con, obs):
    data=obs.model_dump()
    data['created_at']=data['created_at'] or ahora()
    payload={'observer':data['observer'],'visit_date':data['visit_date'],'source':data['source']}
    vid=con.execute("INSERT INTO ib_visits(original,payload,stage,created_at) VALUES(?,?,'committed',?)",
        (data['raw_input'],pack(payload),data['created_at'])).lastrowid
    eid=con.execute('INSERT INTO ib_equipment(identity,modality,payload,visit_id) VALUES(?,?,?,?)',
        (identidad(data),data['modality'],pack(data),vid)).lastrowid
    con.execute('INSERT INTO ib_evidence VALUES(?,?,1,1)',(eid,vid))
    _revision(con,vid,'importación',data)
    return eid


def guardar(obs: Observacion):
    """Importación explícita de semilla; la captura usa consolidar."""
    with closing(conectar()) as con, con:
        return _importar(con,obs)


def guardar_borrador(payload, visit_id=None, version=None):
    Borrador.model_validate(payload['borrador'])
    payload['visit_date']=fecha_visita(payload.get('visit_date'))
    with closing(conectar()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        if visit_id is None:
            visit_id=con.execute('INSERT INTO ib_visits(original,payload,created_at) VALUES(?,?,?)',
                (payload.get('texto_original',''),pack(payload),ahora())).lastrowid
            _revision(con,visit_id,'extracción',payload)
            return visit_id,1
        row=con.execute('SELECT * FROM ib_visits WHERE id=?',(visit_id,)).fetchone()
        if not row or row['stage']!='draft' or row['version']!=version:
            raise ValueError('El borrador cambió o ya se guardó. Retómalo de nuevo.')
        if json.loads(row['payload']) == payload:
            return visit_id,version
        con.execute('UPDATE ib_visits SET payload=?,version=version+1 WHERE id=?',(pack(payload),visit_id))
        _revision(con,visit_id,'revisión',payload)
        return visit_id,version+1


def borradores():
    with closing(conectar()) as con:
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in
                con.execute("SELECT * FROM ib_visits WHERE stage='draft' ORDER BY id DESC")]


def descartar(vid):
    with closing(conectar()) as con, con:
        con.execute("UPDATE ib_visits SET stage='discarded' WHERE id=? AND stage='draft'",(vid,))
        _revision(con,vid,'descartar',{})


def compatible(nueva, actual):
    for field in ('brand','model'):
        if not es_desconocido(nueva.get(field)) and not es_desconocido(actual.get(field)):
            if N.clave(nueva[field]) != N.clave(actual[field]):
                return False
    for field in ('quantity','age_years'):
        if nueva.get(field) is not None and actual.get(field) is not None and nueva[field]!=actual[field]:
            return False
    return True


def confirma(nueva, actual):
    if not compatible(nueva,actual) or nueva.get('quantity') is None:
        return False
    for field in ('quantity','age_years','brand','model'):
        value=actual.get(field)
        if value is not None and value!='Unknown' and nueva.get(field)!=value:
            return False
    return True


def _destinos_automaticos(filas, existentes):
    """Empareja cada grupo capturado con el grupo vigente de su modalidad.

    Es lo que permite guardar sin preguntar nada: si el hospital ya tenia
    registrada esa modalidad, la visita entra como evidencia sobre ese grupo, y
    si lo dicho no cuadra queda anotado como conflicto. Lo que nunca hace es
    crear un grupo paralelo en silencio, porque entonces la base diria que hay
    el doble de equipos y nadie lo notaria.

    Cuando ninguno de los candidatos cuadra se elige el mas reciente a
    proposito: la discrepancia tiene que quedar registrada contra el dato que
    hoy se da por bueno.
    """
    destinos, usados = {}, set()
    for gid, nueva in filas:
        candidatos = [e for e in existentes
                      if e['modality'] == nueva['modality'] and e['id'] not in usados]
        if not candidatos:
            continue
        def frescura(e):
            datos = json.loads(e['payload'])
            return max(datos.get('verified_date', ''), datos.get('visit_date', ''))
        cuadran = [e for e in candidatos if compatible(nueva, json.loads(e['payload']))]
        elegido = sorted(cuadran or candidatos, key=frescura, reverse=True)[0]
        destinos[gid] = elegido['id']
        usados.add(elegido['id'])
    return destinos


def consolidar(vid, version, modo, destinos=None):
    if modo not in {'auto','nuevo','recuento','evidencia','completar'}:
        raise ValueError('Acción inválida.')
    destinos=destinos or {}
    with closing(conectar()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        visita=con.execute('SELECT * FROM ib_visits WHERE id=?',(vid,)).fetchone()
        if not visita or visita['stage']!='draft' or visita['version']!=version:
            raise ValueError('La visita cambió o ya fue guardada; no se duplicó.')
        payload=json.loads(visita['payload'])
        borrador=Borrador.model_validate(payload['borrador'])
        nombre,_=N.normalizar_cliente(borrador.customer)
        if es_desconocido(nombre) or not N.es_nombre_identificable(borrador.customer):
            raise ValueError('Identifica el hospital sin ambigüedad antes de guardar.')
        if not borrador.items or any(e.modality==Modalidad.UNKNOWN for e in borrador.items):
            raise ValueError('Indica la modalidad de cada grupo.')
        fecha=fecha_visita(payload.get('visit_date'))
        filas=[]
        for eq in borrador.items:
            obs=Observacion(customer=borrador.customer,city=borrador.city,country=borrador.country,
                observer=payload.get('observer','Unknown'),visit_date=fecha,modality=eq.modality.value,
                quantity=eq.quantity,brand=eq.brand,model=eq.model,age_years=eq.age_years,
                status=eq.status.value,source=payload.get('source','Text'),raw_input=visita['original'],
                notes=' | '.join(filter(None,[borrador.notes,eq.notes])),
                install_year=date.fromisoformat(fecha).year-eq.age_years if fecha and eq.age_years is not None else None,
                created_at=ahora())
            filas.append((eq.group_id,obs.model_dump()))
        identity=identidad(filas[0][1])
        existentes=[dict(r) for r in con.execute('SELECT * FROM ib_equipment WHERE identity=? AND active=1',(identity,))]
        if modo=='auto':
            destinos=_destinos_automaticos(filas,existentes)
        if modo=='recuento':
            if not fecha or any(r['quantity'] is None for _,r in filas):
                raise ValueError('Un recuento requiere fecha de visita y cantidades conocidas.')
            mods={r['modality'] for _,r in filas}
            for ex in existentes:
                antes=json.loads(ex['payload'])
                if ex['modality'] in mods and max(antes.get('visit_date',''), antes.get('verified_date',''))>fecha:
                    raise ValueError('Hay un recuento más reciente. Guarda esta visita como evidencia.')
            for mod in mods:
                con.execute('UPDATE ib_equipment SET active=0 WHERE identity=? AND modality=?',(identity,mod))
        ids=[]
        usados=set()
        for gid,nueva in filas:
            # En automatico cada grupo decide por su cuenta: el que ya existe se
            # vincula, el que no, se crea. Un mismo modo para toda la visita no
            # sirve cuando trae una modalidad conocida y otra nueva.
            accion = ('evidencia' if gid in destinos else 'nuevo') if modo=='auto' else modo
            if accion in {'nuevo','recuento'}:
                eid=con.execute('INSERT INTO ib_equipment(identity,modality,payload,visit_id) VALUES(?,?,?,?)',
                    (identity,nueva['modality'],pack(nueva),vid)).lastrowid
                con.execute('INSERT INTO ib_evidence VALUES(?,?,1,1)',(eid,vid))
            else:
                eid=destinos.get(gid)
                if eid in usados:
                    raise ValueError('No vincules dos grupos al mismo destino; revisa los subconjuntos.')
                usados.add(eid)
                destino=next((e for e in existentes if e['id']==eid and e['modality']==nueva['modality']),None)
                if destino is None:
                    raise ValueError('Selecciona un grupo vigente del mismo cliente y modalidad.')
                actual=json.loads(destino['payload'])
                coincide=compatible(nueva,actual)
                confirma_todo=confirma(nueva,actual)
                if accion=='completar':
                    if not coincide or (actual['quantity'] is not None and actual['quantity']!=nueva['quantity']):
                        raise ValueError('Completar requiere cantidades compatibles; un detalle parcial no describe toda la flota.')
                    anterior=dict(actual)
                    for field in ('quantity','age_years','brand','model'):
                        if actual.get(field) is None or actual.get(field)=='Unknown':
                            actual[field]=nueva[field]
                    if anterior.get('age_years') is None and nueva.get('age_years') is not None:
                        actual['install_year']=nueva['install_year']
                        actual['age_observed_date']=fecha
                    con.execute('UPDATE ib_equipment SET payload=? WHERE id=?',(pack(actual),eid))
                    _revision(con,vid,'completar',{'equipment_id':eid,'antes':anterior,'después':actual})
                con.execute('INSERT INTO ib_evidence VALUES(?,?,?,?)',(eid,vid,int(coincide),int(confirma_todo)))
                if confirma_todo and fecha and fecha >= (actual.get('verified_date') or actual.get('visit_date','')):
                    actual['verified_date']=fecha
                    con.execute('UPDATE ib_equipment SET payload=? WHERE id=?',(pack(actual),eid))
            ids.append(eid)
        con.execute("UPDATE ib_visits SET stage='committed',version=version+1 WHERE id=?",(vid,))
        _revision(con,vid,'consolidar',{'modo':modo,'equipment_ids':ids,'destinos':destinos})
        return ids


def todas():
    from .confidence import calcular_confianza
    with closing(conectar()) as con:
        result=[]
        for row in con.execute('SELECT * FROM ib_equipment WHERE active=1 ORDER BY id'):
            data=json.loads(row['payload']);data['observation_id']=row['id']
            data['customer_key']=row['identity']
            data.setdefault('verified_date',data.get('visit_date',''))
            data.setdefault('age_observed_date',data.get('visit_date',''))
            evidence=con.execute('''SELECT e.*,v.payload FROM ib_evidence e JOIN ib_visits v ON v.id=e.visit_id
                                   WHERE e.equipment_id=?''',(row['id'],)).fetchall()
            observers={N.clave(json.loads(e['payload']).get('observer','')) for e in evidence
                       if e['compatible'] and e['confirms'] and not es_desconocido(json.loads(e['payload']).get('observer'))}
            data['independent_observers']=len(observers)
            data['evidence_count']=len(evidence)
            data['conflicts']=sum(not e['compatible'] for e in evidence)
            data['confidence_score'],data['confidence']=calcular_confianza(data,[])
            result.append(data)
        return result


def historial(equipment_id):
    with closing(conectar()) as con:
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in con.execute('''
          SELECT r.*,v.original FROM ib_revisions r JOIN ib_visits v ON v.id=r.visit_id
          WHERE r.visit_id IN (SELECT visit_id FROM ib_evidence WHERE equipment_id=?) ORDER BY r.id
        ''',(equipment_id,))]


def historial_cliente(customer_key):
    with closing(conectar()) as con:
        return [{**json.loads(r['payload']), 'observation_id': r['id'], 'vigente': bool(r['active'])}
                for r in con.execute('SELECT * FROM ib_equipment WHERE identity=? ORDER BY id DESC',(customer_key,))]


def por_cliente(customer):
    return [r for r in todas() if N.clave(r['customer'])==N.clave(customer)]


def clientes():
    return sorted({r['customer'] for r in todas()})


def contar():
    return len(todas())


def recalcular_confianza():
    return todas()


def vaciar():
    with closing(conectar()) as con, con:
        for table in ('ib_evidence','ib_equipment','ib_revisions','ib_visits'):
            con.execute(f'DELETE FROM {table}')


def sembrar(forzar=False):
    from .legacy_store import _MAPEO_SEMILLA, _int, _seguimiento
    ruta=DATA_DIR/'seed_installed_base.json'
    if not ruta.exists() or (contar() and not forzar):
        return 0
    if forzar:
        vaciar()
    filas=json.loads(ruta.read_text(encoding='utf-8'))
    for fila in filas:
        data={dest:fila.get(source,'') for source,dest in _MAPEO_SEMILLA.items()}
        for field in ('quantity','age_years','install_year'):
            data[field]=_int(data[field]) or None
        data['source']='Seed'
        data['notes']=' | '.join(filter(None,[fila.get('Notes',''),_seguimiento(fila)]))
        guardar(Observacion(**data))
    return len(filas)


def exportar_csv(ruta: Path):
    filas=todas()
    ruta.parent.mkdir(parents=True,exist_ok=True)
    columns=list(filas[0]) if filas else list(Observacion.model_fields)
    with ruta.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader();writer.writerows(filas)
    return ruta
