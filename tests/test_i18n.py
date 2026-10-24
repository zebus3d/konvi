"""Que no falte ni sobre ninguna traducción:  python3 -m unittest tests.test_i18n"""

import re
import unittest

from konvi import i18n
from konvi.i18n_en import EN
from tests.i18n_keys import all_keys

FIELD = re.compile(r"\{(\w+)\}")


class I18nTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("es")

    def test_no_falta_ninguna(self):
        missing = sorted(all_keys() - EN.keys())
        self.assertEqual(missing, [], "textos sin traducir al inglés")

    def test_no_sobra_ninguna(self):
        stale = sorted(EN.keys() - all_keys())
        self.assertEqual(stale, [], "traducciones de textos que ya no existen")

    def test_mismos_huecos(self):
        for es, en in EN.items():
            self.assertEqual(sorted(FIELD.findall(es)), sorted(FIELD.findall(en)), es)
            self.assertEqual(es.count("\n"), en.count("\n"), f"distintos saltos de línea: {es!r}")

    def test_detectar_idioma(self):
        self.assertEqual(i18n.detect({"LANG": "es_ES.UTF-8"}), "es")
        self.assertEqual(i18n.detect({"LANGUAGE": "es_ES:es:en", "LANG": "en_US.UTF-8"}), "es")
        self.assertEqual(i18n.detect({"LC_ALL": "en_GB.UTF-8", "LANG": "es_ES.UTF-8"}), "en")
        self.assertEqual(i18n.detect({"LANG": "de_DE.UTF-8"}), "en")      # otros idiomas: inglés
        self.assertEqual(i18n.detect({"LANG": "C"}), "en")
        self.assertEqual(i18n.detect({}), "en")

    def test_tr_y_numeros(self):
        self.assertEqual(i18n.tr("Perfil para los {n} vídeos:", n=3), "Perfil para los 3 vídeos:")
        self.assertEqual(i18n.number(2.34), "2,3")
        i18n.set_language("en")
        self.assertEqual(i18n.tr("Perfil para los {n} vídeos:", n=3), "Profile for the 3 videos:")
        self.assertEqual(i18n.tr("Un perfil mío"), "Un perfil mío")      # lo del usuario, tal cual
        self.assertEqual(i18n.number(2.34), "2.3")


if __name__ == "__main__":
    unittest.main()
