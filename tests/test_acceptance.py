from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import store, nlquery, followup, confidence
from src.extract import extraer, extraer_solo_reglas
from src.schema import Borrador, Equipo, Modalidad, Estado, Observacion

NOTE = 'Estoy en Hospital DemoCare Pacific, en Panamá. Tienen dos resonadores y un tomógrafo. Uno de los resonadores parece de unos ocho años.'

class ExtractionTests(unittest.TestCase):
    def test_principal_subset(self):
        b=extraer_solo_reglas(NOTE)
        self.assertEqual(sum(e.quantity for e in b.items if e.modality==Modalidad.MR),2)
        self.assertEqual(sum(e.quantity for e in b.items if e.age_years==8),1)
        self.assertEqual(sum(e.quantity for e in b.items),3)
        self.assertTrue(all(e.brand=='Unknown' and e.model=='Unknown' for e in b.items))
        self.assertEqual(next(e.status for e in b.items if e.modality==Modalidad.CT),Estado.REPORTADO)
    def test_ambiguous_hospital(self):
        for note in ['Estoy en Hospital DemoCare, tienen dos resonadores.','Estoy en el hospital, vi un tomógrafo.']:
            self.assertEqual(extraer_solo_reglas(note).customer,'Unknown')
    def test_strict_failure(self):
        motor=SimpleNamespace(estado=SimpleNamespace(listo=True),json_estructurado=Mock(side_effect=RuntimeError('test')))
        with self.assertRaises(RuntimeError): extraer(NOTE,motor,estricto=True)
    def test_unknown_and_zero(self):
        b=extraer_solo_reglas('Hospital DemoCare Pacific tiene resonadores.')
        self.assertIsNone(b.items[0].quantity)
        b=extraer_solo_reglas('Hospital DemoCare Pacific tiene cero resonadores.')
        self.assertEqual(b.items[0].quantity,0)
    def test_zero_age_not_propagated_to_other_modality(self):
        b=extraer_solo_reglas('Hospital DemoCare Pacific tiene un resonador de cero años y un tomógrafo.')
        self.assertEqual(next(e.age_years for e in b.items if e.modality==Modalidad.MR),0)
        self.assertIsNone(next(e.age_years for e in b.items if e.modality==Modalidad.CT))
    def test_followup_stable_group(self):
        b=Borrador(items=[Equipo(modality=Modalidad.MR),Equipo(modality=Modalidad.CT)])
        p=next(p for p in followup.pendientes(b) if p.indice==1 and p.campo=='quantity')
        omitted={p.clave};b.items.pop(0)
        self.assertFalse(any(p.campo=='quantity' for p in followup.pendientes(b,omitted)))
    def test_unknown_answer_and_age_range(self):
        b=Borrador(items=[Equipo(modality=Modalidad.CT,quantity=1)])
        p=next(p for p in followup.pendientes(b) if p.campo=='age_years')
        self.assertFalse(followup.aplicar_respuesta(b,p,'No lo sé')[1])
        self.assertFalse(followup.aplicar_respuesta(b,p,'entre 5 y 7 años')[1])
        self.assertIsNone(b.items[0].age_years)

class QueryTests(unittest.TestCase):
    def test_country_requests(self):
        motor=SimpleNamespace(estado=SimpleNamespace(listo=True),json_estructurado=Mock(side_effect=AssertionError('LLM unnecessary')))
        for phrase,pais in [('Muéstrame solo los de Panamá','Panama'),('equipos en Brasil','Brazil'),('clientes de México','Mexico'),('Brazil','Brazil'),('todos los de Costa Rica','Costa Rica')]:
            f=nlquery.interpretar(phrase,motor)
            self.assertEqual(f,{**nlquery.FILTRO_VACIO,'pais':pais})
    def test_country_aliases_and_empty(self):
        rows=[dict(customer='A',country='Brasil'),dict(customer='B',country='Brazil'),dict(customer='C',country='Panamá')]
        self.assertEqual(len(nlquery.aplicar(nlquery.interpretar('Brasil',None),rows)),2)
        self.assertEqual(nlquery.aplicar(nlquery.interpretar('México',None),rows),[])
    def test_quantity_not_age(self):
        f=nlquery.interpretar('hospitales con más de cinco resonadores',None)
        self.assertEqual((f['cantidad_min'],f['edad_min']),(6,0))
    def test_combined_filters_correct_wrong_llm_country(self):
        motor=SimpleNamespace(estado=SimpleNamespace(listo=True),json_estructurado=Mock(return_value={**nlquery.FILTRO_VACIO,'pais':'Mexico','modalidad':'MR'}))
        f=nlquery.interpretar('clientes en Brasil con más de cinco resonadores de más de siete años',motor)
        self.assertEqual((f['pais'],f['cantidad_min'],f['edad_min']),('Brazil',6,8))
    def test_zero_age_and_unknown(self):
        rows=[dict(customer='A',age_years=0),dict(customer='B',age_years=None),dict(customer='C',age_years=1)]
        f=nlquery.interpretar('equipos de menos de un año',None)
        self.assertEqual([r['customer'] for r in nlquery.aplicar(f,rows)],['A'])
    def test_country_word_boundaries(self):
        self.assertEqual(nlquery._pais_explicito('equipos en Brasilia')[0],'')
        self.assertEqual(nlquery._pais_explicito('equipos en Ciudad de México')[0],'')
    def test_cohort_quantity_totals(self):
        rows=[dict(customer='A',modality='MR',quantity=3),dict(customer='A',modality='MR',quantity=3),dict(customer='B',modality='MR',quantity=2)]
        self.assertEqual(len(nlquery.aplicar(nlquery.interpretar('más de cinco resonadores',None),rows)),2)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(store,'DB_PATH',Path(self.temp.name)/'test.db');self.patch.start()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def draft(self,items=None,observer='Ana',fecha='2026-01-01'):
        b=Borrador(customer='Hospital DemoCare Pacific',city='Panama City',country='Panama',items=items or [Equipo(modality=Modalidad.MR,quantity=2,status=Estado.REPORTADO)])
        p={'borrador':b.model_dump(mode='json'),'texto_original':'Visita original','observer':observer,'visit_date':fecha,'source':'Text','conversacion':[],'omitidas':[]}
        return b,p,store.guardar_borrador(p)
    def test_three_visits_two_units(self):
        b,p,(vid,v)=self.draft();eid=store.consolidar(vid,v,'nuevo')[0]
        for name in ['Luis','Marta']:
            b,p,(vid,v)=self.draft(observer=name)
            store.consolidar(vid,v,'evidencia',{b.items[0].group_id:eid})
        row=store.todas()[0]
        self.assertEqual((row['quantity'],row['evidence_count'],row['independent_observers']),(2,3,3))
        self.assertEqual(confidence.desglose(row,[])['Confirmaciones'],15)
    def test_conflict_no_confidence_or_freshness(self):
        b,p,(vid,v)=self.draft();eid=store.consolidar(vid,v,'nuevo')[0]
        b,p,(vid,v)=self.draft([Equipo(modality=Modalidad.MR,quantity=7)],observer='Luis',fecha='2026-02-01')
        store.consolidar(vid,v,'evidencia',{b.items[0].group_id:eid})
        row=store.todas()[0]
        self.assertEqual((row['quantity'],row['independent_observers'],row['conflicts'],row['visit_date']),(2,1,1,'2026-01-01'))
    def test_replace_only_reported_modalities(self):
        b,p,(vid,v)=self.draft([Equipo(modality=Modalidad.MR,quantity=2),Equipo(modality=Modalidad.CT,quantity=1)])
        store.consolidar(vid,v,'nuevo')
        b,p,(vid,v)=self.draft([Equipo(modality=Modalidad.MR,quantity=3)],fecha='2026-02-01')
        store.consolidar(vid,v,'recuento')
        self.assertEqual(sorted((r['modality'],r['quantity']) for r in store.todas()),[('CT',1),('MR',3)])
        with closing(store.conectar()) as con: self.assertEqual(con.execute('SELECT count(*) FROM ib_equipment').fetchone()[0],3)
    def test_old_recount_rejected_atomically(self):
        b,p,(vid,v)=self.draft(fecha='2026-02-01');store.consolidar(vid,v,'nuevo')
        b,p,(vid,v)=self.draft(fecha='2026-01-01')
        with self.assertRaises(ValueError):store.consolidar(vid,v,'recuento')
        self.assertEqual(len(store.todas()),1)
    def test_old_recount_after_recent_confirmation(self):
        b,p,(vid,v)=self.draft();eid=store.consolidar(vid,v,'nuevo')[0]
        b,p,(vid,v)=self.draft(observer='Luis',fecha='2026-03-01')
        store.consolidar(vid,v,'evidencia',{b.items[0].group_id:eid})
        b,p,(vid,v)=self.draft(fecha='2026-02-01')
        with self.assertRaises(ValueError):store.consolidar(vid,v,'recuento')
        self.assertEqual(store.todas()[0]['verified_date'],'2026-03-01')
    def test_no_double_save(self):
        b,p,(vid,v)=self.draft();store.consolidar(vid,v,'nuevo')
        with self.assertRaises(ValueError):store.consolidar(vid,v,'nuevo')
        self.assertEqual(len(store.todas()),1)
    def test_draft_revision_and_original_preserved(self):
        b,p,(vid,v)=self.draft();p['conversacion']=[['agente','Marca?'],['colaborador','No lo sé']]
        p['omitidas']=['marca'];p['texto_original']='Texto corregido'
        _,v2=store.guardar_borrador(p,vid,v)
        self.assertEqual(v2,v+1)
        self.assertEqual(store.borradores()[0]['original'],'Visita original')
        self.assertEqual(store.borradores()[0]['payload']['omitidas'],['marca'])
        with self.assertRaises(ValueError):store.guardar_borrador(p,vid,v)
    def test_unknown_date_and_zero_are_preserved(self):
        b,p,(vid,v)=self.draft([Equipo(modality=Modalidad.MR,quantity=0,age_years=0)],fecha='')
        store.consolidar(vid,v,'nuevo');row=store.todas()[0]
        self.assertEqual((row['quantity'],row['age_years'],row['visit_date']),(0,0,''))
        self.assertIsNone(row['install_year']);self.assertTrue(confidence.sin_verificar(row))
    def test_installation_uses_visit_year(self):
        b,p,(vid,v)=self.draft([Equipo(modality=Modalidad.MR,quantity=1,age_years=8)],fecha='2020-01-01')
        store.consolidar(vid,v,'nuevo');self.assertEqual(store.todas()[0]['install_year'],2012)
    def test_future_date_rejected(self):
        with self.assertRaises(ValueError):self.draft(fecha=(date.today()+timedelta(days=1)).isoformat())
    def test_incomplete_enrichment_does_not_change_total(self):
        b,p,(vid,v)=self.draft();eid=store.consolidar(vid,v,'nuevo')[0]
        b,p,(vid,v)=self.draft([Equipo(modality=Modalidad.MR,quantity=1,age_years=8)])
        with self.assertRaises(ValueError):store.consolidar(vid,v,'completar',{b.items[0].group_id:eid})
        self.assertIsNone(store.todas()[0]['age_years'])
    def test_status_values(self):
        for estado in Estado:
            b,p,(vid,v)=self.draft([Equipo(modality=Modalidad.CT,quantity=1,status=estado)])
            eid=store.consolidar(vid,v,'nuevo')[0]
            self.assertEqual(next(r['status'] for r in store.todas() if r['observation_id']==eid),estado.value)

class MigrationTests(unittest.TestCase):
    def test_backup_and_idempotent_migration(self):
        with tempfile.TemporaryDirectory() as d, patch.object(store,'DB_PATH',Path(d)/'legacy.db'):
            row=Observacion(customer='Hospital Legacy',quantity=0,age_years=0).model_dump()
            with closing(sqlite3.connect(store.DB_PATH)) as con, con:
                con.execute('CREATE TABLE observaciones (' + ','.join(k+' '+('INTEGER' if k in {'quantity','age_years','observation_id','install_year','confidence_score'} else 'TEXT') for k in row) + ')')
                con.execute('INSERT INTO observaciones VALUES('+','.join('?' for _ in row)+')',list(row.values()))
            self.assertEqual(len(store.todas()),1)
            self.assertEqual(len(store.todas()),1)
            self.assertIsNone(store.todas()[0]['quantity'])
            self.assertTrue(Path(str(store.DB_PATH)+'.pre-v2.bak').exists())
            with closing(sqlite3.connect(store.DB_PATH)) as con:
                self.assertEqual(con.execute('SELECT quantity FROM observaciones').fetchone()[0],0)


if __name__=='__main__':unittest.main()
