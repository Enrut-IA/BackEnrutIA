# Enrut.IA — Backend y Motor de Optimización Logística (ACO 2.0)

Servicio backend en **Python** con **FastAPI** que geocodifica direcciones en Bogotá, extrae subgrafos viales de **OpenStreetMap (`OSMnx`)** por *Bounding Box* con caché persistente en disco y calcula la secuencia óptima de entregas mediante **Optimización por Colonia de Hormigas (ACO 2.0)** ($\alpha = 1.0$, $\beta = 2.0$, $\rho = 0.5$).

---

## Estructura del Proyecto

```text
BackEnrutIA/
├── app/
│   ├── main.py                  # Instancia de FastAPI, CORS, health check y registro de routers
│   ├── routers/
│   │   └── rutas.py             # Endpoints HTTP para iniciar ruta y obtener lotes de empresa
│   ├── schemas/
│   │   └── ruta_schema.py       # Contratos Pydantic de entrada y salida de datos
│   └── services/
│       └── motorIA.py           # Geocodificación, caché en disco, subgrafos y motor ACO 2.0
├── tests/
│   └── test_motor.py            # Pruebas en consola del motor de optimización
├── cache_grafos/                # Caché local (.graphml y direcciones.json) [Ignorada en Git]
├── requirements.txt             # Dependencias exactas del entorno Python
├── .gitignore                   # Exclusión de entorno virtual, caché y archivos temporales
└── README.md                    # Guía de instalación, arquitectura y pruebas
```

### ¿Para qué sirve cada carpeta, archivo y librería?

* **`app/main.py`:** Crea la aplicación FastAPI, habilita **CORS** para permitir conexiones desde el frontend en `localhost` y expone `GET /` para verificar que el servidor está activo.
* **`app/routers/rutas.py`:** Define los endpoints bajo `/api/v1/rutas` (`POST /iniciar` para calcular la ruta óptima a partir de un punto de salida, pedidos y prioridad `rapida_entrega`, y `GET /empresa/asignados` para consultar un lote predefinido de empresa).
* **`app/schemas/ruta_schema.py`:** Valida los JSON de entrada y salida con **Pydantic**, normaliza el campo `rapida_entrega` (`"si"` / `"No"` o booleano) y acepta coordenadas GPS opcionales junto a la dirección.
* **`app/services/motorIA.py`:** Traduce nomenclatura vial colombiana (`#`, `-`, abreviaturas como `Cr`, `Cl`, `Av`), extrae únicamente el rectángulo de calles necesario (*Bounding Box*), guarda los grafos (`.graphml`) y direcciones (`direcciones.json`) en `cache_grafos/` para reutilizarlos en futuras consultas y ejecuta **ACO 2.0** con autoajuste de hormigas ($m = n$) y parada por estancamiento.
* **`tests/test_motor.py`:** Permite ejecutar pruebas directas en consola sobre el motor de optimización sin necesidad de arrancar el servidor HTTP.
* **Librerías de Python (`requirements.txt`):**
  * `fastapi`: Framework web para construir la API REST y generar la documentación interactiva en Swagger UI.
  * `uvicorn`: Servidor ASGI que ejecuta la aplicación en `localhost`.
  * `osmnx`: Descarga la malla vial vehicular (`network_type="drive"`) desde OpenStreetMap y asocia coordenadas a los nodos más cercanos del grafo.
  * `networkx`: Construye el grafo matemático, calcula distancias reales por calles en metros y obtiene la poligonal entre paradas.
  * `scikit-learn`: Requerida internamente por `osmnx.nearest_nodes()` para la búsqueda espacial de nodos más cercanos.
  * `numpy`: Gestiona la selección probabilística de rutas del enjambre en cada iteración.

---

## Instalación y Puesta en Marcha Local

### 1. Crear el entorno virtual con `venv`
Asegúrate de tener **Python 3.10 o superior** instalado (con la opción *Add Python to PATH* habilitada). Abre una terminal dentro de la carpeta raíz `BackEnrutIA` y ejecuta:

```bash
python -m venv venv
```

### 2. Activar el entorno virtual
Debes activar `venv` cada vez que abras una nueva terminal para trabajar en el backend:

* **Windows (PowerShell):**
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
  .\venv\Scripts\Activate.ps1
  ```
* **Windows (CMD):**
  ```cmd
  venv\Scripts\activate
  ```
* **Linux / macOS:**
  ```bash
  source venv/bin/activate
  ```

Sabrás que el entorno está activo cuando veas **`(venv)`** al inicio de la línea de comandos.

### 3. Instalar las dependencias de Python
Con `(venv)` activo, instala todas las librerías del proyecto desde `requirements.txt`:

```bash
pip install -r requirements.txt
```

*(Si instalas librerías adicionales durante el desarrollo, actualiza el archivo ejecutando `pip freeze > requirements.txt`).*

---

## Levantar el Servidor y Probar en Swagger

### 1. Arrancar FastAPI en `localhost`
Desde la raíz `BackEnrutIA` y con `(venv)` activo, ejecuta:

```bash
uvicorn app.main:app --reload
```

El servidor quedará disponible en `http://127.0.0.1:8000`.

### 2. Probar en Swagger UI
Abre en tu navegador:

👉 **`http://127.0.0.1:8000/docs`**

1. Despliega **`POST /api/v1/rutas/iniciar`** y haz clic en **Try it out**.
2. Pega el siguiente JSON de prueba (sin `lat`/`lon` en `0` para que geocodifique las direcciones):

```json
{
  "direccion_salida": "Portal Suba, Bogotá, Colombia",
  "pedidos": [
    {
      "nombre": "Vanessa Torres",
      "direccion": "Carrera 104, Suba, Bogotá, Colombia",
      "rapida_entrega": false
    },
    {
      "nombre": "Kenny Palacios",
      "direccion": "Calle 145, Suba, Bogotá, Colombia",
      "rapida_entrega": false
    },
    {
      "nombre": "Andrés Gómez",
      "direccion": "Carrera 91, Suba, Bogotá, Colombia",
      "rapida_entrega": false
    }
  ]
}
```

3. Presiona **Execute** y revisa la respuesta `200 OK`: incluye `distancia_total_km`, `tiempo_procesamiento_ms`, `iteraciones_aco`, `pedidos_ordenados` (con estados como `En camino`, `Pendiente` y `Por despachar`) y `ruta_poligonal`. La carpeta `cache_grafos/` se crea automáticamente al consultar una zona nueva, haciendo que las siguientes consultas en esa misma zona respondan en ~1 segundo desde disco.

---

## Flujo en Git

* Trabaja en ramas de funcionalidad (`git checkout -b feature/<nombre>`) y sube tus cambios mediante *Pull Request* hacia `main`.
* Verifica que `venv/`, `cache/`, `cache_grafos/` y `__pycache__/` permanezcan excluidos en `.gitignore`.
