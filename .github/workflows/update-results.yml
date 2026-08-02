#!/usr/bin/env python3
"""Baixa e sanitiza as corridas dos últimos 7 dias do Exilados GP."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REMOTE_BASE = os.environ.get(
    "ACSM_BASE_URL", "https://usa4.assettohosting.com:50709"
).rstrip("/")
DAYS = int(os.environ.get("DAYS_WINDOW", "7"))
MAX_PAGES = 100
MAX_RACES = 100
OUTPUT = Path(os.environ.get("OUTPUT_FILE", "data/races.json"))
USER_AGENT = "Mozilla/5.0 (compatible; ExiladosGP-GitHubSync/1.1)"

# A documentação do ACSM limita a API a 5 requisições em 20 segundos.
MIN_REQUEST_INTERVAL = 4.2
_last_request_at = 0.0


def parse_date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_at = time.monotonic()


def response_preview(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit] or "resposta vazia"


def get_json(url: str, timeout: int = 45, attempts: int = 3) -> dict[str, Any]:
    for attempt in range(1, attempts + 1):
        throttle()
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Cache-Control": "no-cache",
            },
        )
        context = ssl.create_default_context()

        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=context
            ) as response:
                status = int(getattr(response, "status", 200))
                content_type = response.headers.get("Content-Type", "")
                raw = response.read()

            if status < 200 or status >= 300:
                raise RuntimeError(f"HTTP {status} em {url}")

            text = raw.decode("utf-8-sig", errors="replace")
            stripped = text.lstrip()

            # Quando o Manager está fechado, a API costuma devolver a página
            # de login em HTML, embora o navegador autenticado mostre JSON.
            if stripped.startswith("<") or "text/html" in content_type.lower():
                raise RuntimeError(
                    "O endpoint devolveu HTML em vez de JSON. "
                    "Ative Public Access/Make Open em Server > Accounts e "
                    "libere as permissões Results Api List e Results Download. "
                    f"Início da resposta: {response_preview(text)}"
                )

            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Resposta não é um JSON válido. "
                    f"Content-Type: {content_type or 'não informado'}. "
                    f"Início da resposta: {response_preview(text)}"
                ) from exc

            if not isinstance(payload, dict):
                raise RuntimeError(f"Resposta JSON inválida em {url}")

            return payload

        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < attempts:
                retry_after = exc.headers.get("Retry-After")
                try:
                    wait = max(20, int(retry_after or 20))
                except ValueError:
                    wait = 20
                print(f"Limite da API atingido; aguardando {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {exc.code} em {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < attempts:
                wait = 5 * attempt
                print(
                    f"Tentativa {attempt}/{attempts} falhou; aguardando {wait}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"Falha de conexão com {url}: {exc}") from exc

    raise RuntimeError(f"Não foi possível consultar {url}")


def stable_driver_id(guid: Any, name: Any) -> str:
    source = str(guid or "").strip()
    if not source:
        source = re.sub(r"[^A-Z0-9]", "", str(name or "").upper()) or "PILOTO-SEM-ID"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
    return f"driver-{digest}"


def sanitize_result(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("DriverName") or "Piloto sem nome")
    return {
        "DriverId": stable_driver_id(row.get("DriverGuid"), name),
        "DriverName": name,
        "BestLap": int(row.get("BestLap") or 0),
        "TotalTime": int(row.get("TotalTime") or 0),
        "NumLaps": int(row.get("NumLaps") or 0),
        "GridPosition": row.get("GridPosition"),
        "CarModel": str(row.get("CarModel") or ""),
        "Disqualified": bool(row.get("Disqualified")),
        "HasPenalty": bool(row.get("HasPenalty")),
    }


def sanitize_driver(driver: Any) -> dict[str, Any]:
    if not isinstance(driver, dict):
        return {"Id": stable_driver_id("", ""), "Name": ""}
    name = str(driver.get("Name") or "")
    return {"Id": stable_driver_id(driver.get("Guid"), name), "Name": name}


def sanitize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("Type") or "")
    if event_type not in {"COLLISION_WITH_CAR", "COLLISION_WITH_ENV"}:
        return None
    position = event.get("WorldPosition") if isinstance(event.get("WorldPosition"), dict) else {}
    return {
        "Type": event_type,
        "Timestamp": event.get("Timestamp"),
        "AfterSessionEnd": bool(event.get("AfterSessionEnd")),
        "CarId": event.get("CarId"),
        "OtherCarId": event.get("OtherCarId"),
        "Driver": sanitize_driver(event.get("Driver")),
        "OtherDriver": sanitize_driver(event.get("OtherDriver")),
        "WorldPosition": {
            "X": position.get("X", 0),
            "Y": position.get("Y", 0),
            "Z": position.get("Z", 0),
        },
    }


def sanitize_race(raw: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    results = raw.get("Result") if isinstance(raw.get("Result"), list) else []
    events = raw.get("Events") if isinstance(raw.get("Events"), list) else []
    clean_events = [
        item
        for event in events
        if isinstance(event, dict) and (item := sanitize_event(event))
    ]
    return {
        "Type": "RACE",
        "Date": raw.get("Date") or metadata.get("date"),
        "SessionFile": raw.get("SessionFile")
        or Path(str(metadata.get("results_json_url", "resultado"))).stem,
        "TrackName": raw.get("TrackName")
        or metadata.get("track")
        or "pista_desconhecida",
        "TrackConfig": raw.get("TrackConfig") or metadata.get("track_layout"),
        "Result": [sanitize_result(row) for row in results if isinstance(row, dict)],
        "Events": clean_events,
    }


def fetch_recent_races() -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    selected: dict[str, dict[str, Any]] = {}
    total_pages = 1

    for page in range(MAX_PAGES):
        if page >= total_pages:
            break

        query = urllib.parse.urlencode({"q": "", "page": page, "sort": "date"})
        listing = get_json(f"{REMOTE_BASE}/api/results/list.json?{query}")

        if page == 0:
            total_pages = max(1, min(MAX_PAGES, int(listing.get("num_pages") or 1)))

        items = listing.get("results") if isinstance(listing.get("results"), list) else []
        page_has_recent = False

        for item in items:
            if not isinstance(item, dict):
                continue
            date = parse_date(str(item.get("date") or ""))
            if date is None:
                continue
            if date >= cutoff:
                page_has_recent = True
            if date < cutoff or str(item.get("session_type") or "").upper() != "RACE":
                continue

            path = str(item.get("results_json_url") or "")
            if not re.fullmatch(r"/results/download/[A-Za-z0-9_.-]+\.json", path):
                continue

            selected[path] = item
            if len(selected) >= MAX_RACES:
                break

        if not page_has_recent or len(selected) >= MAX_RACES:
            break

    races: list[dict[str, Any]] = []
    for path, metadata in selected.items():
        raw = get_json(f"{REMOTE_BASE}{path}")
        clean = sanitize_race(raw, metadata)
        if clean["Result"]:
            races.append(clean)

    races.sort(
        key=lambda race: parse_date(str(race.get("Date") or ""))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return races


def content_without_timestamp(payload: dict[str, Any]) -> str:
    comparable = dict(payload)
    comparable.pop("generated_at", None)
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    try:
        races = fetch_recent_races()
        if not races:
            raise RuntimeError(f"Nenhuma corrida encontrada nos últimos {DAYS} dias.")

        payload = {
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": DAYS,
            "race_count": len(races),
            "races": races,
        }

        if OUTPUT.exists():
            try:
                old = json.loads(OUTPUT.read_text(encoding="utf-8"))
                if isinstance(old, dict) and content_without_timestamp(old) == content_without_timestamp(payload):
                    print("Nenhuma mudança nos resultados.")
                    return 0
            except (OSError, json.JSONDecodeError):
                pass

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Arquivo atualizado: {OUTPUT} ({len(races)} corridas)")
        return 0
    except (RuntimeError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
