import os
import json
import math
import time
import logging
import threading
import numpy as np
import osmnx as ox
import networkx as nx
import requests
from osmnx._errors import InsufficientResponseError, ResponseStatusCodeError

from app.services.geocodificador import geocodificar_punto, ErrorServidorMapas

ox.settings.log_console = False
ox.settings.use_cache = True
ox.settings.requests_timeout = 120
ox.settings.overpass_rate_limit = False
try:
    ox.settings.http_user_agent = "EnrutIA_Bogota_Backend/1.0"
except AttributeError:
    pass

# Carpeta persistente en disco para grafos (.graphml) y direcciones (.json)
CARPETA_CACHE = "cache_grafos"
ARCHIVO_CUADROS_VACIOS = os.path.join(CARPETA_CACHE, "cuadros_vacios.json")
os.makedirs(CARPETA_CACHE, exist_ok=True)

TAMANO_CUADRO = 0.02
MARGEN_CUADRO = 0.002
SERVIDORES_OVERPASS = [
    "https://overpass-api.de/api",
    "https://maps.mail.ru/osm/tools/overpass/api",
]
ESPERAS_REINTENTO = (0, 5, 10, 20)
MAX_GRAFOS_EN_MEMORIA = 8

registro = logging.getLogger(__name__)
_cuadros_en_memoria = {}
_grafos_combinados = {}
_candado_cuadros = threading.Lock()


def _indices_cuadros(sur: float, norte: float, oeste: float, este: float) -> list:
    filas = range(math.floor(sur / TAMANO_CUADRO), math.floor(norte / TAMANO_CUADRO) + 1)
    columnas = range(math.floor(oeste / TAMANO_CUADRO), math.floor(este / TAMANO_CUADRO) + 1)
    return [(fila, columna) for fila in filas for columna in columnas]


def _cargar_cuadros_vacios() -> set:
    if os.path.exists(ARCHIVO_CUADROS_VACIOS):
        try:
            with open(ARCHIVO_CUADROS_VACIOS, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def _guardar_cuadro_vacio(nombre: str):
    vacios = _cargar_cuadros_vacios()
    vacios.add(nombre)
    with open(ARCHIVO_CUADROS_VACIOS, "w", encoding="utf-8") as f:
        json.dump(sorted(vacios), f)


def _pedir_grafo(sur: float, norte: float, oeste: float, este: float):
    opciones = {"network_type": "drive", "simplify": False, "retain_all": True, "truncate_by_edge": True}
    try:
        return ox.graph_from_bbox(bbox=(oeste, sur, este, norte), **opciones)
    except TypeError:
        return ox.graph_from_bbox(norte, sur, este, oeste, **opciones)


def _es_zona_sin_calles(error: Exception) -> bool:
    texto = str(error)
    return texto.startswith("No data elements") or "no graph nodes" in texto.lower()


def _limites_cuadro(fila: int, columna: int) -> tuple:
    return (
        fila * TAMANO_CUADRO - MARGEN_CUADRO,
        (fila + 1) * TAMANO_CUADRO + MARGEN_CUADRO,
        columna * TAMANO_CUADRO - MARGEN_CUADRO,
        (columna + 1) * TAMANO_CUADRO + MARGEN_CUADRO,
    )


def _descargar_zona(sur: float, norte: float, oeste: float, este: float):
    servidor_original = ox.settings.overpass_url
    sin_conexion = True
    ultimo_error = None
    try:
        for espera in ESPERAS_REINTENTO:
            time.sleep(espera)
            for servidor in SERVIDORES_OVERPASS:
                ox.settings.overpass_url = servidor
                try:
                    return _pedir_grafo(sur, norte, oeste, este)
                except ResponseStatusCodeError as e:
                    sin_conexion = False
                    ultimo_error = e
                except requests.ConnectionError as e:
                    ultimo_error = e
                except requests.RequestException as e:
                    sin_conexion = False
                    ultimo_error = e
                except (InsufficientResponseError, ValueError) as e:
                    if _es_zona_sin_calles(e):
                        return None
                    sin_conexion = False
                    ultimo_error = e
    finally:
        ox.settings.overpass_url = servidor_original

    registro.warning("No se pudo descargar la zona (%s, %s, %s, %s): %s", sur, norte, oeste, este, ultimo_error)
    if sin_conexion:
        raise ErrorServidorMapas(
            "No se pudo descargar el mapa de la zona. Revisa tu conexión a internet e intenta de nuevo."
        )
    raise ErrorServidorMapas(
        "El servidor de mapas está ocupado en este momento. Intenta calcular la ruta de nuevo en unos segundos."
    )


def _recortar_cuadro(grafo, fila: int, columna: int):
    if grafo is None:
        return None
    sur, norte, oeste, este = _limites_cuadro(fila, columna)
    try:
        cuadro = ox.truncate.truncate_graph_bbox(grafo, (oeste, sur, este, norte), truncate_by_edge=True)
    except ValueError:
        return None
    return cuadro if cuadro.number_of_nodes() > 0 else None


def _obtener_cuadros(cuadros: list) -> list:
    vacios = _cargar_cuadros_vacios()
    faltantes = []
    for fila, columna in cuadros:
        nombre = f"{fila}_{columna}"
        archivo = os.path.join(CARPETA_CACHE, f"cuadro_{nombre}.graphml")
        if (fila, columna) in _cuadros_en_memoria:
            continue
        if os.path.exists(archivo):
            _cuadros_en_memoria[(fila, columna)] = ox.load_graphml(archivo)
        elif nombre in vacios:
            _cuadros_en_memoria[(fila, columna)] = None
        else:
            faltantes.append((fila, columna))

    if faltantes:
        limites = [_limites_cuadro(fila, columna) for fila, columna in faltantes]
        zona = _descargar_zona(
            min(l[0] for l in limites),
            max(l[1] for l in limites),
            min(l[2] for l in limites),
            max(l[3] for l in limites),
        )
        for fila, columna in faltantes:
            nombre = f"{fila}_{columna}"
            cuadro = _recortar_cuadro(zona, fila, columna)
            if cuadro is None:
                _guardar_cuadro_vacio(nombre)
            else:
                ox.save_graphml(cuadro, os.path.join(CARPETA_CACHE, f"cuadro_{nombre}.graphml"))
            _cuadros_en_memoria[(fila, columna)] = cuadro

    return [_cuadros_en_memoria[c] for c in cuadros if _cuadros_en_memoria[c] is not None]


def obtener_subgrafo_bbox(coordenadas: list, margen: float = 0.008):
    """Une los cuadros de la zona guardados en disco y descarga solo los que falten."""
    lats = [c[0] for c in coordenadas]
    lons = [c[1] for c in coordenadas]

    cuadros = _indices_cuadros(min(lats) - margen, max(lats) + margen, min(lons) - margen, max(lons) + margen)
    clave = tuple(sorted(cuadros))
    with _candado_cuadros:
        if clave in _grafos_combinados:
            return _grafos_combinados[clave]

        grafos = _obtener_cuadros(cuadros)
        if not grafos:
            raise ValueError("No se encontraron calles en la zona de las direcciones.")

        combinado = ox.simplify_graph(nx.compose_all(grafos))
        comp_mayor = max(nx.strongly_connected_components(combinado), key=len)
        grafo_final = combinado.subgraph(comp_mayor).copy()

        if len(_grafos_combinados) >= MAX_GRAFOS_EN_MEMORIA:
            _grafos_combinados.pop(next(iter(_grafos_combinados)))
        _grafos_combinados[clave] = grafo_final
        return grafo_final


def calcular_matriz_distancias(grafo, nodos_unicos: list) -> dict:
    """Calcula distancias reales en metros entre nodos únicos."""
    matriz = {}
    for origen in nodos_unicos:
        matriz[origen] = {}
        for destino in nodos_unicos:
            if origen == destino:
                matriz[origen][destino] = 0
            else:
                matriz[origen][destino] = nx.shortest_path_length(
                    grafo, source=origen, target=destino, weight="length"
                )
    return matriz


def optimizar_ruta_aco(
    grafo,
    nodo_salida: int,
    nodos_pedidos: list,
    nodos_prioritarios: set,
    num_iteraciones: int = 150
):
    """Motor ACO 2.0 con autoajuste de hormigas y criterio de estancamiento."""
    if not nodos_pedidos:
        return [nodo_salida], 0.0, 0

    puntos_totales = [nodo_salida] + nodos_pedidos
    num_hormigas = len(puntos_totales)
    tolerancia_estancamiento = max(15, len(puntos_totales) // 2)

    matriz_distancias = calcular_matriz_distancias(grafo, puntos_totales)

    tasa_evaporacion = 0.5
    alfa = 1.0
    beta = 2.0
    feromonas = {
        origen: {destino: 1.0 for destino in puntos_totales if origen != destino}
        for origen in puntos_totales
    }

    mejor_ruta_global = None
    mejor_distancia_global = float("inf")
    iteraciones_ejecutadas = 0
    iteraciones_sin_mejora = 0

    for iteracion in range(num_iteraciones):
        iteraciones_ejecutadas = iteracion + 1
        rutas_hormigas = []
        distancias_hormigas = []
        hubo_mejora = False

        for _ in range(num_hormigas):
            ruta_actual = [nodo_salida]
            distancia_total = 0

            while len(ruta_actual) < len(puntos_totales):
                nodo_actual = ruta_actual[-1]
                no_visitados = [n for n in puntos_totales if n not in ruta_actual]

                probabilidades = []
                suma_prob = 0.0

                for destino in no_visitados:
                    dist = matriz_distancias[nodo_actual][destino]
                    visibilidad = (1.0 / dist) if dist > 0 else 0.0
                    if destino in nodos_prioritarios:
                        visibilidad *= 2.5

                    feromona = feromonas[nodo_actual][destino]
                    prob = (feromona ** alfa) * (visibilidad ** beta)
                    probabilidades.append(prob)
                    suma_prob += prob

                probabilidades = [p / suma_prob for p in probabilidades]
                siguiente_nodo = int(np.random.choice(no_visitados, p=probabilidades))

                ruta_actual.append(siguiente_nodo)
                distancia_total += matriz_distancias[nodo_actual][siguiente_nodo]

            rutas_hormigas.append(ruta_actual)
            distancias_hormigas.append(distancia_total)

            if distancia_total < mejor_distancia_global:
                mejor_distancia_global = distancia_total
                mejor_ruta_global = ruta_actual
                hubo_mejora = True

        if hubo_mejora:
            iteraciones_sin_mejora = 0
        else:
            iteraciones_sin_mejora += 1

        for origen in feromonas:
            for destino in feromonas[origen]:
                feromonas[origen][destino] *= (1.0 - tasa_evaporacion)

        for i in range(num_hormigas):
            ruta = rutas_hormigas[i]
            aporte = 1000.0 / distancias_hormigas[i]
            for j in range(len(ruta) - 1):
                feromonas[ruta[j]][ruta[j + 1]] += aporte

        if iteraciones_sin_mejora >= tolerancia_estancamiento:
            break

    return mejor_ruta_global, mejor_distancia_global, iteraciones_ejecutadas


def calcular_ruta_entregas(salida: dict, lista_pedidos: list) -> dict:
    """Orquesta geocodificación, carga de subgrafo, ACO 2.0 y trazado poligonal."""
    t_inicio = time.perf_counter()

    lat_sal, lon_sal = geocodificar_punto(salida["direccion"], salida.get("lat"), salida.get("lon"))

    pedidos_geocodificados = []
    for p in lista_pedidos:
        lat_p, lon_p = geocodificar_punto(p["direccion"], p.get("lat"), p.get("lon"), (lat_sal, lon_sal))
        pedidos_geocodificados.append({**p, "lat": lat_p, "lon": lon_p})

    todas_coords = [(lat_sal, lon_sal)] + [(p["lat"], p["lon"]) for p in pedidos_geocodificados]
    grafo = obtener_subgrafo_bbox(todas_coords)

    nodo_salida = int(ox.nearest_nodes(grafo, X=lon_sal, Y=lat_sal))
    pedidos_por_nodo = {}
    nodos_prioritarios = set()

    for p in pedidos_geocodificados:
        nodo = int(ox.nearest_nodes(grafo, X=p["lon"], Y=p["lat"]))
        if nodo == nodo_salida:
            continue
        pedidos_por_nodo.setdefault(nodo, []).append(p)
        if p["rapida_entrega"]:
            nodos_prioritarios.add(nodo)

    nodos_unicos_pedidos = list(pedidos_por_nodo.keys())
    ruta_nodos, distancia_metros, iteraciones = optimizar_ruta_aco(
        grafo, nodo_salida, nodos_unicos_pedidos, nodos_prioritarios
    )

    trazado_gps = []
    for i in range(len(ruta_nodos) - 1):
        tramo = nx.shortest_path(grafo, ruta_nodos[i], ruta_nodos[i + 1], weight="length")
        nodos_tramo = tramo if i == 0 else tramo[1:]
        for n in nodos_tramo:
            trazado_gps.append({"lat": grafo.nodes[n]["y"], "lon": grafo.nodes[n]["x"]})

    pedidos_ordenados = [
        {
            "orden": 1,
            "tipo": "salida",
            "nombre": "Punto de partida",
            "direccion": salida["direccion"],
            "rapida_entrega": False,
            "estado": "Iniciado",
            "lat": lat_sal,
            "lon": lon_sal,
        }
    ]

    contador_parada = 2
    for idx_nodo, nodo in enumerate(ruta_nodos[1:], start=1):
        for p in pedidos_por_nodo[nodo]:
            if idx_nodo == 1:
                estado_pedido = "En camino"
            elif idx_nodo == 2:
                estado_pedido = "Pendiente"
            else:
                estado_pedido = "Por despachar"

            pedidos_ordenados.append({
                "orden": contador_parada,
                "tipo": "pedido",
                "nombre": p["nombre"],
                "direccion": p["direccion"],
                "rapida_entrega": p["rapida_entrega"],
                "estado": estado_pedido,
                "lat": p["lat"],
                "lon": p["lon"],
            })
            contador_parada += 1

    tiempo_ms = round((time.perf_counter() - t_inicio) * 1000, 2)

    return {
        "estado_proceso": "ok",
        "distancia_total_km": round(distancia_metros / 1000.0, 2),
        "tiempo_procesamiento_ms": tiempo_ms,
        "iteraciones_aco": iteraciones,
        "pedidos_ordenados": pedidos_ordenados,
        "ruta_poligonal": trazado_gps,
    }