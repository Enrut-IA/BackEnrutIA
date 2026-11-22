import json
import math
import os
import re
import time

import requests
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union

CARPETA_CACHE = "cache_grafos"
ARCHIVO_DIRECCIONES = os.path.join(CARPETA_CACHE, "direcciones.json")
ARCHIVO_CALLES = os.path.join(CARPETA_CACHE, "calles_bogota.json")
os.makedirs(CARPETA_CACHE, exist_ok=True)

LIMITES_BOGOTA = (4.45, -74.25, 4.84, -73.99)
SERVIDORES_OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
URL_NOMINATIM = "https://nominatim.openstreetmap.org/search"
CABECERAS = {"User-Agent": "EnrutIA_Bogota_Backend/1.0"}

LAT_BASE = 4.65
METROS_POR_GRADO_LAT = 110574.0
METROS_POR_GRADO_LON = 111320.0 * math.cos(math.radians(LAT_BASE))

VIAS_TIPO_CARRERA = ("carrera", "transversal")
VIAS_TIPO_CALLE = ("calle", "diagonal")
LETRAS = ("", "a", "b", "c", "d", "e", "f")
RANGO_INTERPOLACION = 3
PLACA_MAXIMA_M = 150
TOLERANCIA_CRUCE_M = 40

ABREVIATURAS = [
    (r"\b(ak|av\.?\s*cra|av\.?\s*kr|avenida\s+cra)\.?(?=\s|\d)", "avenida carrera "),
    (r"\b(ac|av\.?\s*cl|av\.?\s*calle|avenida\s+cl)\.?(?=\s|\d)", "avenida calle "),
    (r"\b(cr|cra|kr|kra|carrera)\.?(?=\s|\d)", "carrera "),
    (r"\b(cl|cll|calle)\.?(?=\s|\d)", "calle "),
    (r"\b(dg|diag|diagonal)\.?(?=\s|\d)", "diagonal "),
    (r"\b(tv|tr|transv|transversal)\.?(?=\s|\d)", "transversal "),
]

PATRON_DIRECCION = re.compile(
    r"^(?P<tipo>avenida carrera|avenida calle|carrera|calle|diagonal|transversal)\s+"
    r"(?P<num>\d+)\s*(?P<letra>[a-z](?![a-z]))?\s*(?P<bis>bis)?\s*(?P<sufijo1>sur|este)?\s*"
    r"(?:#|no\.?|n°|nro\.?|numero|número)?\s*"
    r"(?P<cruce>\d+)\s*(?P<letra_cruce>[a-z](?![a-z]))?\s*(?P<bis_cruce>bis)?\s*"
    r"(?:-\s*(?P<placa>\d+))?\s*(?P<sufijo2>sur|este)?"
)

_indice_calles = None


class ErrorServidorMapas(Exception):
    pass


def _cargar_cache() -> dict:
    if os.path.exists(ARCHIVO_DIRECCIONES):
        try:
            with open(ARCHIVO_DIRECCIONES, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _guardar_cache(cache: dict):
    with open(ARCHIVO_DIRECCIONES, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _dentro_de_bogota(lat: float, lon: float) -> bool:
    sur, oeste, norte, este = LIMITES_BOGOTA
    return sur <= lat <= norte and oeste <= lon <= este


def _a_metros(lat: float, lon: float) -> tuple:
    return (lon * METROS_POR_GRADO_LON, (lat - LAT_BASE) * METROS_POR_GRADO_LAT)


def _a_grados(x: float, y: float) -> tuple:
    return (y / METROS_POR_GRADO_LAT + LAT_BASE, x / METROS_POR_GRADO_LON)


def _normalizar_nombre(nombre: str) -> str:
    texto = re.sub(r"\s+", " ", nombre.strip().lower())
    return re.sub(r"(\d) ([a-z])(?![a-z])", r"\1\2", texto)


def _descargar_calles() -> dict:
    sur, oeste, norte, este = LIMITES_BOGOTA
    consulta = (
        "[out:json][timeout:600];"
        f'way["highway"]["name"~"^(Avenida )?(Calle|Carrera|Diagonal|Transversal) [0-9]"]'
        f"({sur},{oeste},{norte},{este});"
        "out tags geom;"
    )
    for intento in range(3):
        for servidor in SERVIDORES_OVERPASS:
            try:
                respuesta = requests.post(servidor, data={"data": consulta}, headers=CABECERAS, timeout=900)
                if respuesta.ok and respuesta.content.startswith(b"{"):
                    elementos = respuesta.json().get("elements", [])
                    if elementos:
                        return _compactar_calles(elementos)
            except (requests.RequestException, ValueError):
                pass
        time.sleep(10 * (intento + 1))
    raise requests.RequestException("No fue posible descargar las calles de Bogotá desde OpenStreetMap.")


def _compactar_calles(elementos: list) -> dict:
    calles = {}
    for via in elementos:
        geometria = via.get("geometry") or []
        if len(geometria) < 2:
            continue
        puntos = [[round(p["lat"], 6), round(p["lon"], 6)] for p in geometria]
        etiquetas = via.get("tags", {})
        nombres = {_normalizar_nombre(etiquetas[k]) for k in ("name", "alt_name", "official_name") if etiquetas.get(k)}
        for nombre in nombres:
            calles.setdefault(nombre, []).append(puntos)
    return calles


def _obtener_indice() -> dict:
    global _indice_calles
    if _indice_calles is not None:
        return _indice_calles

    if os.path.exists(ARCHIVO_CALLES):
        try:
            with open(ARCHIVO_CALLES, "r", encoding="utf-8") as f:
                _indice_calles = json.load(f)
                return _indice_calles
        except Exception:
            pass

    calles = _descargar_calles()
    with open(ARCHIVO_CALLES, "w", encoding="utf-8") as f:
        json.dump(calles, f, ensure_ascii=False, separators=(",", ":"))
    _indice_calles = calles
    return _indice_calles


def interpretar_direccion(texto: str):
    limpio = re.sub(r",.*$", "", texto.strip().lower())
    for patron, reemplazo in ABREVIATURAS:
        limpio = re.sub(patron, reemplazo, limpio)
    limpio = re.sub(r"\s+", " ", limpio).strip()

    m = PATRON_DIRECCION.match(limpio)
    if not m:
        return None

    tipo = m.group("tipo")
    return {
        "tipo": tipo,
        "num": int(m.group("num")),
        "letra": m.group("letra") or "",
        "bis": bool(m.group("bis")),
        "cruce": int(m.group("cruce")),
        "letra_cruce": m.group("letra_cruce") or "",
        "bis_cruce": bool(m.group("bis_cruce")),
        "placa": min(int(m.group("placa") or 0), PLACA_MAXIMA_M),
        "sufijo": m.group("sufijo1") or m.group("sufijo2") or "",
        "es_carrera": "carrera" in tipo or tipo == "transversal",
    }


def _nombres_via(tipos: tuple, numero: int, letra: str, bis: bool, sufijo: str) -> list:
    base = f"{numero}{letra}{' bis' if bis else ''}{' ' + sufijo if sufijo else ''}"
    return [f"{prefijo}{tipo} {base}" for tipo in tipos for prefijo in ("", "avenida ")]


def _valor_via(numero: int, letra: str, bis: bool) -> float:
    valor = float(numero)
    if letra:
        valor += (ord(letra) - 96) * 0.1
    if bis:
        valor += 0.05
    return round(valor, 2)


def _lineas(indice: dict, nombres: list) -> list:
    lineas = []
    for nombre in nombres:
        for puntos in indice.get(nombre, []):
            lineas.append(LineString([_a_metros(lat, lon) for lat, lon in puntos]))
    return lineas


def _puntos_cruce(principal, cruce) -> list:
    interseccion = principal.intersection(cruce)
    if interseccion.is_empty:
        a, b = nearest_points(principal, cruce)
        if a.distance(b) <= TOLERANCIA_CRUCE_M:
            return [Point((a.x + b.x) / 2, (a.y + b.y) / 2)]
        return []
    if interseccion.geom_type == "Point":
        return [interseccion]
    return [g if g.geom_type == "Point" else g.centroid for g in getattr(interseccion, "geoms", [])]


def _elegir_punto(puntos: list, referencia):
    base = min(puntos, key=lambda p: p.distance(referencia)) if referencia is not None else puntos[0]
    cercanos = [p for p in puntos if p.distance(base) <= 80]
    return Point(sum(p.x for p in cercanos) / len(cercanos), sum(p.y for p in cercanos) / len(cercanos))


def _ubicar_por_cruce(datos: dict, referencia_m):
    indice = _obtener_indice()
    es_carrera = datos["es_carrera"]
    tipos_principal = VIAS_TIPO_CARRERA if es_carrera else VIAS_TIPO_CALLE
    tipos_cruce = VIAS_TIPO_CALLE if es_carrera else VIAS_TIPO_CARRERA

    sufijo = datos["sufijo"]
    sufijo_en_principal = (sufijo == "sur" and not es_carrera) or (sufijo == "este" and es_carrera)
    sufijo_principal = sufijo if sufijo_en_principal else ""
    sufijo_cruce = "" if sufijo_en_principal else sufijo

    lineas_principal = _lineas(
        indice, _nombres_via(tipos_principal, datos["num"], datos["letra"], datos["bis"], sufijo_principal)
    )
    if not lineas_principal:
        return None
    principal = unary_union(lineas_principal)

    esquinas = {}
    cruce = datos["cruce"]
    for numero in range(max(cruce - RANGO_INTERPOLACION, 1), cruce + RANGO_INTERPOLACION + 1):
        for letra in LETRAS:
            for bis in (False, True):
                lineas = _lineas(indice, _nombres_via(tipos_cruce, numero, letra, bis, sufijo_cruce))
                if not lineas:
                    continue
                puntos = _puntos_cruce(principal, unary_union(lineas))
                if puntos:
                    esquinas[_valor_via(numero, letra, bis)] = _elegir_punto(puntos, referencia_m)

    if not esquinas:
        return None

    objetivo = _valor_via(cruce, datos["letra_cruce"], datos["bis_cruce"])
    metodo = "cruce_vial"
    if objetivo in esquinas:
        esquina = esquinas[objetivo]
    else:
        menores = [v for v in esquinas if v < objetivo]
        mayores = [v for v in esquinas if v > objetivo]
        if not menores or not mayores:
            return None
        v1, v2 = max(menores), min(mayores)
        p1, p2 = esquinas[v1], esquinas[v2]
        if p1.distance(p2) > 1500:
            return None
        t = (objetivo - v1) / (v2 - v1)
        esquina = Point(p1.x + (p2.x - p1.x) * t, p1.y + (p2.y - p1.y) * t)
        metodo = "interpolacion"

    punto = esquina
    if datos["placa"] > 0:
        punto = _avanzar_placa(principal, esquina, esquinas, objetivo, datos)

    lat, lon = _a_grados(punto.x, punto.y)
    return lat, lon, metodo


def _avanzar_placa(principal, esquina, esquinas: dict, objetivo: float, datos: dict):
    tramo = min(getattr(principal, "geoms", [principal]), key=lambda g: g.distance(esquina))
    d0 = tramo.project(esquina)
    adelante = tramo.interpolate(min(d0 + datos["placa"], tramo.length))
    atras = tramo.interpolate(max(d0 - datos["placa"], 0))

    siguientes = [v for v in esquinas if v > objetivo and esquinas[v].distance(esquina) > 5]
    if siguientes:
        siguiente = esquinas[min(siguientes)]
        direccion = (siguiente.x - esquina.x, siguiente.y - esquina.y)
    elif datos["es_carrera"]:
        direccion = (0, -1) if datos["sufijo"] == "sur" else (0, 1)
    else:
        direccion = (1, 0) if datos["sufijo"] == "este" else (-1, 0)

    def avance(p):
        return (p.x - esquina.x) * direccion[0] + (p.y - esquina.y) * direccion[1]

    return adelante if avance(adelante) >= avance(atras) else atras


def _coincide_con_direccion(resultado: dict, datos: dict) -> bool:
    if resultado.get("category") == "highway":
        return False
    detalle = resultado.get("address", {})
    via = _normalizar_nombre(detalle.get("road", ""))
    tipos = VIAS_TIPO_CARRERA if datos["es_carrera"] else VIAS_TIPO_CALLE
    nombres = _nombres_via(tipos, datos["num"], datos["letra"], datos["bis"], datos["sufijo"])
    nombres += _nombres_via(tipos, datos["num"], datos["letra"], datos["bis"], "")
    numero = re.sub(r"\s+", "", detalle.get("house_number", "").lower())
    return via in nombres and re.match(rf"#?{datos['cruce']}{datos['letra_cruce']}\b", numero) is not None


def _consultar_nominatim(direccion: str, datos):
    sur, oeste, norte, este = LIMITES_BOGOTA
    texto = direccion if "bogot" in direccion.lower() else f"{direccion}, Bogotá, Colombia"
    parametros = {
        "q": texto,
        "format": "jsonv2",
        "countrycodes": "co",
        "viewbox": f"{oeste},{norte},{este},{sur}",
        "bounded": 1,
        "limit": 5,
        "addressdetails": 1,
    }
    time.sleep(1.0)
    respuesta = requests.get(URL_NOMINATIM, params=parametros, headers=CABECERAS, timeout=30)
    respuesta.raise_for_status()
    for resultado in respuesta.json():
        if datos is not None and not _coincide_con_direccion(resultado, datos):
            continue
        lat, lon = float(resultado["lat"]), float(resultado["lon"])
        if _dentro_de_bogota(lat, lon):
            return lat, lon
    return None


def geocodificar_punto(direccion: str, lat: float = None, lon: float = None, referencia: tuple = None) -> tuple:
    """Retorna (lat, lon) desde coordenadas directas, caché en disco, cruce de vías o Nominatim."""
    return ubicar_direccion(direccion, lat, lon, referencia)[:2]


def ubicar_direccion(direccion: str, lat: float = None, lon: float = None, referencia: tuple = None) -> tuple:
    if lat is not None and lon is not None:
        return float(lat), float(lon), "manual"

    cache = _cargar_cache()
    clave = direccion.strip().lower()
    guardado = cache.get(clave)
    if isinstance(guardado, dict):
        return guardado["lat"], guardado["lon"], guardado["metodo"]

    datos = interpretar_direccion(direccion)
    resultado = None
    servidor_caido = False

    if datos is not None:
        referencia_m = Point(_a_metros(*referencia)) if referencia else None
        try:
            resultado = _ubicar_por_cruce(datos, referencia_m)
        except requests.RequestException:
            servidor_caido = True

    if resultado is None:
        try:
            encontrado = _consultar_nominatim(direccion, datos)
        except (requests.RequestException, ValueError):
            encontrado = None
            servidor_caido = True
        if encontrado:
            resultado = (*encontrado, "nominatim")

    if resultado is None and servidor_caido:
        raise ErrorServidorMapas(
            "El servidor de mapas está ocupado en este momento. Intenta de nuevo en unos segundos."
        )

    if resultado is None or not _dentro_de_bogota(resultado[0], resultado[1]):
        raise ValueError(
            f"No se pudo ubicar la dirección '{direccion}' en el mapa de Bogotá. "
            "Revisa la dirección o marca el punto en el mapa."
        )

    lat_res, lon_res, metodo = resultado
    cache[clave] = {"lat": round(lat_res, 7), "lon": round(lon_res, 7), "metodo": metodo}
    _guardar_cache(cache)
    return cache[clave]["lat"], cache[clave]["lon"], metodo
