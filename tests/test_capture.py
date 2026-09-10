import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from src import capture

class Archivo(io.BytesIO):
    def __init__(self,name,data):
        super().__init__(data);self.name=name

class CaptureTests(unittest.TestCase):
    def test_text_and_documents(self):
        e=capture.preparar('Visita en Panamá',[Archivo('nota.txt',b'Dos resonadores'),Archivo('otra.txt',b'Un tomografo')],None,None)
        self.assertEqual(e.fuente,'Document')
        self.assertIn('Visita en Panamá',e.texto)
        self.assertIn('Dos resonadores',e.texto)
        self.assertIn('Un tomografo',e.texto)
        self.assertEqual(e.nombres,['nota.txt','otra.txt'])
    def test_audio_cleanup_and_text(self):
        paths=[]
        def guardar(data,path):
            paths.append(path);path.write_bytes(data);return path
        motor=SimpleNamespace(transcribir=Mock(return_value='Dos resonadores'))
        with patch('src.capture.guardar_audio',side_effect=guardar),patch('src.capture.duracion_segundos',return_value=3):
            e=capture.preparar('Nota',[Archivo('visita.mp3',b'audio')],Archivo('grabacion.wav',b'audio'),motor)
        self.assertEqual(e.fuente,'Voice');self.assertIn('Nota',e.texto)
        self.assertEqual(motor.transcribir.call_count,2)
        self.assertTrue(all(not p.exists() for p in paths))
    def test_unsupported_not_silently_ignored(self):
        with self.assertRaises(ValueError):capture.preparar('nota',[Archivo('imagen.png',b'png')],None,None)
    def test_combined_limit(self):
        with patch('src.capture.documents.MAX_BYTES',5):
            with self.assertRaises(ValueError):capture.preparar('',[Archivo('a.txt',b'123'),Archivo('b.txt',b'456')],None,None)
    def test_transcription_error_cleans_temp(self):
        paths=[]
        def guardar(data,path):
            paths.append(path);path.write_bytes(data);return path
        with patch('src.capture.guardar_audio',side_effect=guardar),patch('src.capture.duracion_segundos',return_value=3):
            with self.assertRaises(RuntimeError):capture.preparar('',[],Archivo('grabacion.wav',b'audio'),SimpleNamespace(transcribir=Mock(side_effect=RuntimeError('fallo'))))
        self.assertTrue(all(not p.exists() for p in paths))
if __name__=='__main__':unittest.main()
