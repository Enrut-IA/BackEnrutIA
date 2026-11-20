import os
import re
import json
import time
import numpy as np
import osmnx as ox
import networkx as nx

ox.settings.log_console = False
ox.settings.use_cache = True
ox.settings.requests_timeout = 120
try:
    ox.settings.http_user_agent = "EnrutIA_Bogota_Backend/1.0"
except AttributeError:
    pass

# Carpeta persistente en disco para grafos (.graphml) y direcciones (.json)
CARPETA_CACHE = "cache_grafos"
ARCHIVO_DIRECCIONES = os.path.join(CARPETA_CACHE, "direcciones.json")
os.makedirs(CARPETA_CACHE, exist_ok=True)


def _cargar_cache_direcciones() -> dict:
    if os.path.exists(ARCHIVO_DIRECCIONES):
        try:
            with open(ARCHIVO_DIRECCIONES, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _guardar_cache_direcciones(cache: dict):
    with open(ARCHIVO_DIRECCIONES, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _traducir_direccion_colombiana(texto: str) -> list:
    """
    Convierte nomenclatura vial colombiana con '#' y '-' (ej. 'Carrera 103A #141-45'
    o 'Cr 104 # 139') en cruces viales que OpenStreetMap puede geocodificar.
    """
    limpio = re.sub(r"\s+", " ", texto).strip()

    abreviaturas = [
        (r"\b(cr|cra|kr|kra)\.?\b", "Carrera"),
        (r"\b(cl|cll)\.?\b", "Calle"),
        (r"\b(av|ak|ac)\.?\b", "Avenida"),
        (r"\b(dg|diag)\.?\b", "Diagonal"),
        (r"\b(tv|transv)\.?\b", "Transversal"),
    ]
    for patron, reemplazo in abreviaturas:
        limpio = re.sub(patron, reemplazo, limpio, flags=re.IGNORECASE)

    consultas = []
    match = re.match(r"^(.*?)\s*(?:#|No\.?|N°)\s*(\d+[A-Za-z]?)(?:\s*-\s*\d+)?(.*)$", limpio, flags=re.IGNORECASE)
    if match:
        via_principal = match.group(1).strip()
        num_cruce = match.group(2).strip()

        if re.search(r"\b(carrera|avenida carrera)\b", via_principal, flags=re.IGNORECASE):
            via_cruce = f"Calle {num_cruce}"
        elif re.search(r"\b(calle|avenida calle)\b", via_principal, flags=re.IGNORECASE):
            via_cruce = f"Carrera {num_cruce}"
        else:
            via_cruce = f"Calle {num_cruce}"

        consultas.append(f"{via_principal} & {via_cruce}, Bogotá, Colombia")
        consultas.append(f"{via_cruce}, {via_principal}, Bogotá, Colombia")

    sin_simbolos = re.sub(r"[#-]", " ", limpio)
    sin_simbolos = re.sub(r"\s+", " ", sin_simbolos).strip()
    if "bogotá" not in sin_simbolos.lower() and "bogota" not in sin_simbolos.lower():
        consultas.append(f"{sin_simbolos}, Bogotá, Colombia")
    else:
        consultas.append(sin_simbolos)

    return list(dict.fromkeys(consultas))


def geocodificar_punto(direccion: str, lat: float = None, lon: float = None) -> tuple:
    """Retorna (lat, lon) desde coordenadas directas, caché JSON en disco o Nominatim."""
    if lat is not None and lon is not None:
        return float(lat), float(lon)

    cache_dirs = _cargar_cache_direcciones()
    clave = direccion.strip().lower()
    if clave in cache_dirs:
        return tuple(cache_dirs[clave])

    consultas = _traducir_direccion_colombiana(direccion)
    for consulta in consultas:
        try:
            time.sleep(1.0)
            lat_res, lon_res = ox.geocode(consulta)
            coords = [float(lat_res), float(lon_res)]
            cache_dirs[clave] = coords
            _guardar_cache_direcciones(cache_dirs)
            return tuple(coords)
        except Exception:
            continue

    raise ValueError(f"No se pudo ubicar la dirección '{direccion}' en el mapa de Bogotá.")


def obtener_subgrafo_bbox(coordenadas: list, margen: float = 0.008):
    """
    Carga el subgrafo desde disco (.graphml) si la zona ya fue consultada antes.
    Si es una zona nueva, la descarga por Bounding Box y la guarda en disco.
    """
    lats = [c[0] for c in coordenadas]
    lons = [c[1] for c in coordenadas]

    norte = round(max(lats) + margen, 2)
    sur = round(min(lats) - margen, 2)
    este = round(max(lons) + margen, 2)
    oeste = round(min(lons) - margen, 2)

    archivo_grafo = os.path.join(CARPETA_CACHE, f"grafo_{sur}_{norte}_{oeste}_{este}.graphml")

    if os.path.exists(archivo_grafo):
        return ox.load_graphml(archivo_grafo)

    try:
        grafo = ox.graph_from_bbox(bbox=(oeste, sur, este, norte), network_type="drive", truncate_by_edge=True)
    except TypeError:
        grafo = ox.graph_from_bbox(norte, sur, este, oeste, network_type="drive", truncate_by_edge=True)

    comp_mayor = max(nx.strongly_connected_components(grafo), key=len)
    grafo_final = grafo.subgraph(comp_mayor).copy()

    ox.save_graphml(grafo_final, archivo_grafo)
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
        lat_p, lon_p = geocodificar_punto(p["direccion"], p.get("lat"), p.get("lon"))
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