from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(
    prefix="/api/v1/perfiles",
    tags=["Perfiles"]
)

PERFILES_FIJOS = [
    {
        "id": "rep-01",
        "nombre": "Yazid Sanchez",
        "correo": "yazid@enrutia.com",
        "password": "123",
        "empresa_codigo": "ENRUT-01",
        "zona": "Suba, Bogotá"
    },
    {
        "id": "rep-02",
        "nombre": "Vanessa Torres",
        "correo": "vanessa@enrutia.com",
        "password": "456",
        "empresa_codigo": "ENRUT-01",
        "zona": "Chapinero / Teusaquillo, Bogotá"
    }
]

class SolicitudLoginSimple(BaseModel):
    correo: str
    password: str


@router.post("/ingresar")
def ingresar_perfil_fijo(datos: SolicitudLoginSimple):
    for perfil in PERFILES_FIJOS:
        if perfil["correo"] == datos.correo and perfil["password"] == datos.password:
            return {
                "estado": "ok",
                "perfil": {
                    "id": perfil["id"],
                    "nombre": perfil["nombre"],
                    "correo": perfil["correo"],
                    "empresa_codigo": perfil["empresa_codigo"],
                    "zona": perfil["zona"]
                }
            }
    raise HTTPException(status_code=401, detail="Credenciales inválidas para los perfiles de prueba")


@router.get("/")
def listar_perfiles_demo():
    return [
        {
            "id": p["id"],
            "nombre": p["nombre"],
            "correo": p["correo"],
            "empresa_codigo": p["empresa_codigo"],
            "zona": p["zona"]
        }
        for p in PERFILES_FIJOS
    ]