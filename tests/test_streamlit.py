"""Pruebas de Streamlit con motor simulado y SQLite temporal."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import streamlit as st
from streamlit.testing.v1 import AppTest
from src import store
from src.qvac_engine import MotorQVAC
from src.schema import Borrador,Equipo,Modalidad,Observacion


class StreamlitTests(unittest.TestCase):
    def setUp(self):
        st.cache_resource.clear()
        self.stack=ExitStack()
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(store,'DB_PATH',Path(folder)/'ui.db'))
        self.stack.enter_context(patch.object(store,'sembrar',return_value=0))
        self.motor=MotorQVAC();self.motor.estado.listo=True
        self.stack.enter_context(patch('src.qvac_engine.obtener_motor',return_value=self.motor))
        self.stack.enter_context(patch.object(self.motor,'iniciar_en_segundo_plano'))
    def tearDown(self):
        st.cache_resource.clear()
        self.stack.close()
    def app(self):
        a=AppTest.from_file('app.py').run(timeout=20)
        self.assertFalse(a.exception)
        return a
    def button(self,a,label):
        return next(b for b in a.button if b.label==label)
    def draft(self,a,quantity=None):
        a.session_state['borrador']=Borrador(customer='Hospital Nuevo Real',city='Panama City',country='Panama',items=[Equipo(modality=Modalidad.CT,quantity=quantity)])
        a.session_state['texto_original']='Hospital Nuevo Real tiene tomógrafos'
        a.run(timeout=20)
        self.assertFalse(a.exception)
    def test_unknown_save_and_resume(self):
        a=self.app();self.draft(a)
        self.assertEqual(len(store.borradores()),1)
        a=self.app()
        self.button(a,'Retomar borrador').click().run(timeout=20)
        self.assertFalse(a.exception)
        self.assertEqual(a.session_state['borrador'].customer,'Hospital Nuevo Real')
        self.button(a,'Confirmar y guardar').click().run(timeout=20)
        self.assertFalse(a.exception)
        self.assertEqual(len(store.todas()),1)
        self.assertIsNone(store.todas()[0]['quantity'])
        self.assertFalse(store.borradores())
    def test_evidence_ui_does_not_add_units(self):
        eid=store.guardar(Observacion(customer='Hospital Nuevo Real',city='Panama City',country='Panama',modality='CT',quantity=2,observer='Ana'))
        a=self.app();self.draft(a,2)
        # Ya no se elige destino a mano: el grupo vigente de esa modalidad se
        # empareja solo, asi que guardar es un unico clic.
        self.button(a,'Confirmar y guardar').click().run(timeout=20)
        self.assertFalse(a.exception)
        self.assertEqual([(r['observation_id'],r['quantity']) for r in store.todas()],[(eid,2)])
        self.assertEqual(store.todas()[0]['evidence_count'],2)
    def test_country_no_results_message(self):
        store.guardar(Observacion(customer='Hospital Nuevo Real',country='Panama',modality='CT',quantity=1))
        a=self.app()
        next(t for t in a.text_input if t.label=='Tu pregunta').input('equipos en Brasil').run(timeout=20)
        self.assertFalse(a.exception)
        self.assertTrue(any('No hay observaciones en Brazil' in x.value for x in a.info))
    def test_loading_preserves_note(self):
        self.motor.estado.listo=False
        a=self.app()
        self.assertTrue(self.button(a,'Extraer datos').disabled)
        a.text_area[0].input('Nota escrita durante la carga').run(timeout=20)
        self.motor.estado.listo=True;a.run(timeout=20)
        self.assertFalse(a.exception)
        self.assertFalse(self.button(a,'Extraer datos').disabled)
        self.assertEqual(a.text_area[0].value,'Nota escrita durante la carga')


if __name__=='__main__':unittest.main()
