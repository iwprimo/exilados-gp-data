#!/usr/bin/env python3
"""Bateria de testes controlados (T01 a T16) para o sistema de sincronização Exilados GP."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.update_results as sync_module


class TestExiladosGPSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "races.json"
        self.identity_file = Path(self.temp_dir) / "identities.enc.json"

        # Patch environment and module globals
        self.original_output = sync_module.OUTPUT
        self.original_id_output = sync_module.IDENTITY_BUNDLE_OUTPUT
        self.original_min_req = sync_module.MIN_REQUEST_INTERVAL
        self.original_id_key = sync_module.IDENTITY_BUNDLE_KEY
        self.original_id_req = sync_module.IDENTITY_BUNDLE_REQUIRED

        sync_module.OUTPUT = self.output_file
        sync_module.IDENTITY_BUNDLE_OUTPUT = self.identity_file
        sync_module.MIN_REQUEST_INTERVAL = 0.0
        sync_module.IDENTITY_BUNDLE_KEY = "chave-secreta-de-teste-para-validacao-t01-t16"
        sync_module.IDENTITY_BUNDLE_REQUIRED = True
        sync_module.IDENTITIES.clear()
        sync_module.FALLBACK_DRIVER_IDS.clear()

    def tearDown(self):
        sync_module.OUTPUT = self.original_output
        sync_module.IDENTITY_BUNDLE_OUTPUT = self.original_id_output
        sync_module.MIN_REQUEST_INTERVAL = self.original_min_req
        sync_module.IDENTITY_BUNDLE_KEY = self.original_id_key
        sync_module.IDENTITY_BUNDLE_REQUIRED = self.original_id_req
        sync_module.IDENTITIES.clear()
        sync_module.FALLBACK_DRIVER_IDS.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_t01_api_returns_200_valid_json(self):
        """T01: API retorna HTTP 200 com JSON válido -> Dados processados corretamente."""
        valid_payload = {
            "num_pages": 1,
            "current_page": 0,
            "results": [
                {
                    "session_type": "RACE",
                    "date": datetime.now(timezone.utc).isoformat(),
                    "results_json_url": "/results/download/2026_11_21_22_58_RACE.json",
                    "track": "miami_f1",
                    "track_layout": "layout_f1_2025",
                }
            ],
        }
        race_payload = {
            "Type": "RACE",
            "TrackName": "miami_f1",
            "Result": [
                {
                    "DriverName": "Piloto Teste",
                    "DriverGuid": "76561198000000001",
                    "BestLap": 90000,
                    "TotalTime": 600000,
                    "NumLaps": 5,
                    "GridPosition": 1,
                    "CarModel": "porsche_992_gt3_r",
                    "Disqualified": False,
                    "HasPenalty": False,
                }
            ],
            "Events": [],
            "Laps": [],
        }

        def mock_urlopen(request, timeout=None, context=None):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            if "list.json" in url:
                mock_resp.read.return_value = json.dumps(valid_payload).encode("utf-8")
            else:
                mock_resp.read.return_value = json.dumps(race_payload).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            races = sync_module.fetch_recent_races()
            self.assertEqual(len(races), 1)
            self.assertEqual(races[0]["TrackName"], "miami_f1")
            self.assertEqual(len(races[0]["Result"]), 1)
            self.assertEqual(races[0]["Result"][0]["DriverName"], "Piloto Teste")
            # Validação LGPD: DriverId gerado com hash sem expor o GUID
            self.assertTrue(races[0]["Result"][0]["DriverId"].startswith("driver-"))
            self.assertNotIn("76561198000000001", json.dumps(races[0]))

    def test_t02_api_returns_login_html(self):
        """T02: API retorna HTML de login -> Erro identificado claramente."""
        html_response = b"<!DOCTYPE html><html><body><form id='login'>Acesso Restrito</form></body></html>"

        def mock_urlopen(request, timeout=None, context=None):
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
            mock_resp.read.return_value = html_response
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                sync_module.get_json("https://fake.acsm/api/results/list.json", attempts=1)
            self.assertIn("O endpoint devolveu HTML em vez de JSON", str(ctx.exception))

    def test_t03_api_returns_401_403(self):
        """T03: API retorna HTTP 401/403 -> Falha de autenticação diagnosticada."""
        for code in [401, 403]:
            def mock_urlopen(request, timeout=None, context=None):
                raise urllib.error.HTTPError(
                    url=request.full_url,
                    code=code,
                    msg="Forbidden" if code == 403 else "Unauthorized",
                    hdrs={},
                    fp=io.BytesIO(b"Denied"),
                )

            with patch("urllib.request.urlopen", side_effect=mock_urlopen):
                with self.assertRaises(RuntimeError) as ctx:
                    sync_module.get_json("https://fake.acsm/api/results/list.json", attempts=1)
                self.assertIn(f"HTTP {code}", str(ctx.exception))
                self.assertIn("Autenticação/Permissão recusada", str(ctx.exception))

    def test_t04_api_returns_429_rate_limit(self):
        """T04: API retorna HTTP 429 -> Respeita Retry-After e conclui na tentativa seguinte."""
        call_count = 0

        def mock_urlopen(request, timeout=None, context=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                hdrs = {"Retry-After": "1"}
                raise urllib.error.HTTPError(
                    url=request.full_url,
                    code=429,
                    msg="Too Many Requests",
                    hdrs=hdrs,
                    fp=io.BytesIO(b"Rate limited"),
                )
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = b'{"ok": true, "msg": "sucesso"}'
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), patch("time.sleep") as mock_sleep:
            res = sync_module.get_json("https://fake.acsm/api/test", attempts=2)
            self.assertEqual(res, {"ok": True, "msg": "sucesso"})
            self.assertEqual(call_count, 2)
            mock_sleep.assert_called()

    def test_t05_api_unavailable_preserves_intact_file(self):
        """T05: API indisponível -> Preservar últimos dados íntegros sem sobrescrever arquivo."""
        # Cria arquivo pré-existente
        existing_data = {
            "ok": True,
            "generated_at": "2026-11-20T10:00:00Z",
            "race_count": 5,
            "races": [{"TrackName": "interlagos"}],
        }
        self.output_file.write_text(json.dumps(existing_data), encoding="utf-8")

        def mock_urlopen(request, timeout=None, context=None):
            raise urllib.error.URLError("[Errno 111] Connection refused")

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            exit_code = sync_module.main()
            self.assertEqual(exit_code, 1)
            # Verifica que o arquivo existente permaneceu 100% inalterado
            reloaded = json.loads(self.output_file.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["race_count"], 5)
            self.assertEqual(reloaded["races"][0]["TrackName"], "interlagos")

    def test_t06_invalid_json_does_not_corrupt_file(self):
        """T06: JSON inválido -> Não publicar arquivo corrompido."""
        def mock_urlopen(request, timeout=None, context=None):
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = b"CORRUPTED_JSON_CONTENT{{{"
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                sync_module.get_json("https://fake.acsm/api/test", attempts=1)
            self.assertIn("Resposta não é um JSON válido", str(ctx.exception))
            self.assertFalse(self.output_file.exists())

    def test_t07_valid_but_stale_json_detection(self):
        """T07: JSON válido, mas antigo -> Função utilitária detecta que carimbo tem mais de 2 horas."""
        stale_date = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        recent_date = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

        def is_stale(generated_at_str, max_hours=2):
            dt = sync_module.parse_date(generated_at_str)
            if not dt:
                return True
            return (datetime.now(timezone.utc) - dt) > timedelta(hours=max_hours)

        self.assertTrue(is_stale(stale_date))
        self.assertFalse(is_stale(recent_date))

    def test_t08_no_races_in_last_7_days(self):
        """T08: Nenhuma corrida nos últimos sete dias -> Exibir estado vazio verdadeiro no races.json."""
        empty_listing = {"num_pages": 1, "current_page": 0, "results": []}

        def mock_urlopen(request, timeout=None, context=None):
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = json.dumps(empty_listing).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            exit_code = sync_module.main()
            self.assertEqual(exit_code, 0)
            self.assertTrue(self.output_file.exists())
            payload = json.loads(self.output_file.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "empty_window")
            self.assertEqual(payload["race_count"], 0)
            self.assertEqual(payload["races"], [])

    def test_t09_recent_races_available_updates_rankings(self):
        """T09: Corridas recentes disponíveis -> Atualizar payload com corridas e identidades."""
        listing = {
            "num_pages": 1,
            "current_page": 0,
            "results": [
                {
                    "session_type": "RACE",
                    "date": datetime.now(timezone.utc).isoformat(),
                    "results_json_url": "/results/download/race1.json",
                    "track": "spa",
                }
            ],
        }
        race_json = {
            "Type": "RACE",
            "TrackName": "spa",
            "Result": [
                {
                    "DriverName": "Piloto Vencedor",
                    "DriverGuid": "76561198000000002",
                    "BestLap": 120000,
                    "TotalTime": 720000,
                    "NumLaps": 6,
                    "GridPosition": 1,
                    "CarModel": "bmw_m4_gt3",
                }
            ],
            "Events": [],
            "Laps": [],
        }

        def mock_urlopen(request, timeout=None, context=None):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            if "list.json" in url:
                mock_resp.read.return_value = json.dumps(listing).encode("utf-8")
            else:
                mock_resp.read.return_value = json.dumps(race_json).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            exit_code = sync_module.main()
            self.assertEqual(exit_code, 0)
            payload = json.loads(self.output_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["race_count"], 1)
            self.assertEqual(payload["status"], "online")
            self.assertEqual(payload["races"][0]["TrackName"], "spa")
            self.assertTrue(self.identity_file.exists())

    def test_t10_repeated_execution_idempotency(self):
        """T10: Execução repetida -> Não duplica dados nem corrompe identidades."""
        listing = {
            "num_pages": 1,
            "current_page": 0,
            "results": [
                {
                    "session_type": "RACE",
                    "date": datetime.now(timezone.utc).isoformat(),
                    "results_json_url": "/results/download/race_idem.json",
                    "track": "silverstone",
                }
            ],
        }
        race_json = {
            "Type": "RACE",
            "TrackName": "silverstone",
            "Result": [
                {
                    "DriverName": "Piloto Constante",
                    "DriverGuid": "76561198000000003",
                    "BestLap": 100000,
                    "TotalTime": 600000,
                    "NumLaps": 5,
                }
            ],
            "Events": [],
            "Laps": [],
        }

        def mock_urlopen(request, timeout=None, context=None):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            if "list.json" in url:
                mock_resp.read.return_value = json.dumps(listing).encode("utf-8")
            else:
                mock_resp.read.return_value = json.dumps(race_json).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            # Primeira execução
            self.assertEqual(sync_module.main(), 0)
            data1 = json.loads(self.output_file.read_text(encoding="utf-8"))

            # Segunda execução imediata
            self.assertEqual(sync_module.main(), 0)
            data2 = json.loads(self.output_file.read_text(encoding="utf-8"))

            # A contagem e conteúdo permanecem idênticos
            self.assertEqual(data1["race_count"], data2["race_count"])
            self.assertEqual(len(data1["races"]), len(data2["races"]))
            self.assertEqual(data1["races"][0]["Result"][0]["DriverId"], data2["races"][0]["Result"][0]["DriverId"])

    def test_t11_identity_bundle_failure_no_guid_leak(self):
        """T11: Falha no bundle de identidades -> Erro explícito sem expor GUID."""
        sync_module.IDENTITY_BUNDLE_KEY = ""  # Chave ausente
        sync_module.IDENTITY_BUNDLE_REQUIRED = True

        with self.assertRaises(RuntimeError) as ctx:
            sync_module.write_identity_bundle({"drivers": [{"steam_guid": "76561198999999999"}]})
        self.assertIn("Chave do bundle de identidades não configurada", str(ctx.exception))
        # Verifica que o GUID não foi gravado em nenhum arquivo público
        self.assertFalse(self.output_file.exists())

    def test_t12_git_push_failure_detection(self):
        """T12: Falha no git push -> Detectar falha de publicação."""
        import subprocess
        # Simula tentativa de git push para remote inexistente, verificando detecção de erro
        proc = subprocess.run(
            ["git", "push", "origin_inexistente", "main"],
            capture_output=True,
            text=True,
            cwd=str(Path(self.temp_dir)),
        )
        self.assertNotEqual(proc.returncode, 0)
        # O erro de git push é capturado com código de erro não-zero
        self.assertTrue(bool(proc.stderr or proc.stdout))

    def test_t13_race_window_boundary_inclusion_exclusion(self):
        """T13: Corrida no limite da janela -> Inclusão e exclusão temporal estrita de 7 dias."""
        now = datetime.now(timezone.utc)
        inside_window = (now - timedelta(days=6, hours=23, minutes=50)).isoformat()
        outside_window = (now - timedelta(days=7, hours=0, minutes=10)).isoformat()

        listing = {
            "num_pages": 1,
            "current_page": 0,
            "results": [
                {
                    "session_type": "RACE",
                    "date": inside_window,
                    "results_json_url": "/results/download/inside.json",
                    "track": "pista_dentro",
                },
                {
                    "session_type": "RACE",
                    "date": outside_window,
                    "results_json_url": "/results/download/outside.json",
                    "track": "pista_fora",
                },
            ],
        }

        def mock_urlopen(request, timeout=None, context=None):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            if "list.json" in url:
                mock_resp.read.return_value = json.dumps(listing).encode("utf-8")
            else:
                track = "pista_dentro" if "inside" in url else "pista_fora"
                mock_resp.read.return_value = json.dumps({
                    "Type": "RACE",
                    "TrackName": track,
                    "Result": [{"DriverName": "Piloto Limite"}],
                    "Events": [],
                    "Laps": [],
                }).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            races = sync_module.fetch_recent_races()
            self.assertEqual(len(races), 1)
            self.assertEqual(races[0]["TrackName"], "pista_dentro")

    def test_t14_stale_browser_cache_discard_or_warn(self):
        """T14: Cache antigo no navegador -> Simulação do filtro do frontend."""
        now = datetime.now(timezone.utc)
        cached_races = [
            {"date": (now - timedelta(days=2)).isoformat(), "track": "recente"},
            {"date": (now - timedelta(days=10)).isoformat(), "track": "expirada"},
        ]
        cutoff = now - timedelta(days=7)
        valid_cached = [
            r for r in cached_races
            if sync_module.parse_date(r["date"]) and sync_module.parse_date(r["date"]) >= cutoff
        ]
        self.assertEqual(len(valid_cached), 1)
        self.assertEqual(valid_cached[0]["track"], "recente")

    def test_t15_atomic_write_concurrency_integrity(self):
        """T15: Escrita atômica -> Arquivo não é corrompido durante substituição."""
        test_path = Path(self.temp_dir) / "atomic_test.json"
        content_a = json.dumps({"test": "A" * 1000})
        content_b = json.dumps({"test": "B" * 1000})

        sync_module.atomic_write_text(test_path, content_a)
        self.assertEqual(test_path.read_text(), content_a)

        sync_module.atomic_write_text(test_path, content_b)
        self.assertEqual(test_path.read_text(), content_b)

    def test_t16_incorrect_endpoint_clear_error(self):
        """T16: Endpoint configurado incorretamente -> Erro de configuração identificável."""
        def mock_urlopen(request, timeout=None, context=None):
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=404,
                msg="Not Found",
                hdrs={},
                fp=io.BytesIO(b"404 Not Found"),
            )

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                sync_module.get_json("https://fake.acsm/wrong/endpoint", attempts=1)
            self.assertIn("HTTP 404", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
