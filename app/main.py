from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import rutas

app = FastAPI(
    title="Enrut.IA - API Backend",
    description="Servicio REST de optimización logística mediante Colonia de Hormigas (ACO 2.0).",
    version="1.0.0"
)

# Habilitar CORS para permitir peticiones desde el Frontend (Web o Móvil en localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrar los módulos de endpoints
app.include_router(rutas.router)


@app.get("/", tags=["Estado"])
def health_check():
    return {
        "servicio": "Enrut.IA Backend",
        "estado": "activo",
        "documentacion": "/docs"
    }