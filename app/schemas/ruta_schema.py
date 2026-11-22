from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class CoordenadaGPS(BaseModel):
    lat: float
    lon: float



class PedidoEntrada(BaseModel):
    nombre: str = Field(..., min_length=2, description="Nombre del destinatario")
    direccion: str = Field(..., min_length=5, description="Nomenclatura vial (ej. Carrera 103A #141-45)")
    rapida_entrega: bool = Field(default=False, description="Prioridad del envío (si / No)")
    lat: Optional[float] = Field(default=None, description="Latitud opcional si el frontend la envía")
    lon: Optional[float] = Field(default=None, description="Longitud opcional si el frontend la envía")

    @field_validator("direccion")
    @classmethod
    def limpiar_espacios_direccion(cls, valor: str) -> str:
        valor_limpio = valor.strip()
        if not valor_limpio:
            raise ValueError("La dirección no puede estar vacía.")
        return valor_limpio

    @field_validator("rapida_entrega", mode="before")
    @classmethod
    def normalizar_rapida_entrega(cls, valor):
        if isinstance(valor, bool):
            return valor
        if isinstance(valor, str):
            return valor.strip().lower() in ("si", "sí", "true", "1")
        return False


class SolicitudIniciarRuta(BaseModel):
    direccion_salida: str = Field(
        default="Portal Suba, Bogotá, Colombia",
        description="Punto de partida del repartidor o bodega"
    )
    lat_salida: Optional[float] = Field(default=None)
    lon_salida: Optional[float] = Field(default=None)
    pedidos: List[PedidoEntrada] = Field(..., min_length=1)



class SolicitudGeocodificar(BaseModel):
    direccion: str = Field(..., min_length=5)
    lat_referencia: Optional[float] = Field(default=None)
    lon_referencia: Optional[float] = Field(default=None)


class RespuestaGeocodificar(BaseModel):
    direccion: str
    lat: float
    lon: float
    metodo: str


class ParadaRutaSalida(BaseModel):
    orden: int
    tipo: str               # "salida" o "pedido"
    nombre: str
    direccion: str
    rapida_entrega: bool
    estado: str             # "Punto de partida", "En camino", "Pendiente", "Por despachar"
    lat: float
    lon: float


class RespuestaRutaOptimizada(BaseModel):
    estado_proceso: str
    distancia_total_km: float
    tiempo_procesamiento_ms: float
    iteraciones_aco: int
    pedidos_ordenados: List[ParadaRutaSalida]
    ruta_poligonal: List[CoordenadaGPS]