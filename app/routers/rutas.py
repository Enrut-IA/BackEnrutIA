from fastapi import APIRouter, HTTPException
from app.schemas.ruta_schema import SolicitudIniciarRuta, RespuestaRutaOptimizada
from app.services.motorIA import calcular_ruta_entregas
router = APIRouter(
    prefix="/api/v1/rutas",
    tags=["Rutas y Entregas"]
)


@router.post("/iniciar", response_model=RespuestaRutaOptimizada)
def iniciar_ruta_optimizada(solicitud: SolicitudIniciarRuta):
    """
    Endpoint ejecutado al presionar 'Inicia tu ruta' en 'Lista de Entregas'.
    Acepta nomenclatura vial colombiana (ej. 'Carrera 103A #141-45') y prioridad 'rapida_entrega'.
    """
    try:
        salida = {
            "direccion": solicitud.direccion_salida,
            "lat": solicitud.lat_salida,
            "lon": solicitud.lon_salida,
        }
        pedidos = [p.model_dump() for p in solicitud.pedidos]

        return calcular_ruta_entregas(salida, pedidos)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno calculando la ruta: {str(e)}")


@router.get("/empresa/asignados", response_model=SolicitudIniciarRuta)
def obtener_paquetes_empresa():
    """
    Endpoint para el botón 'Paquetes asignados por tu empresa' de la pantalla Home.
    Devuelve un lote con direcciones reales en Suba listo para optimizar.
    """
    return {
        "direccion_salida": "Portal Suba, Bogotá, Colombia",
        "pedidos": [
            {
                "nombre": "Vanessa Torres",
                "direccion": "Carrera 103A #141-45",
                "rapida_entrega": True
            },
            {
                "nombre": "Kenny Palacios",
                "direccion": "Carrera 104 # 139",
                "rapida_entrega": False
            },
            {
                "nombre": "Camila Rojas",
                "direccion": "Calle 145 # 90",
                "rapida_entrega": False
            }
        ]
    }