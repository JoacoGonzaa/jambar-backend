import os
from datetime import date, datetime
from fastapi import FastAPI, HTTPException, status, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Agenda de Depilación Premium")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

HORAS_ESTANDAR = ['09:00', '10:00', '11:00', '12:00', '15:00', '16:00', '17:00']

class BloqueoAdmin(BaseModel):
    fecha: str
    hora: str
    bloquear: bool

class Cita(BaseModel):
    nombre_cliente: str
    whatsapp_cliente: str
    servicio: str
    fecha_hora: datetime

# --- 💅 ENDPOINTS DE SERVICIOS (NUEVO) ---

@app.get("/api/servicios")
def obtener_servicios():
    try:
        response = supabase.table("servicios").select("*").order("creado_en").execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/servicios")
async def crear_servicio(
    nombre: str = Form(...),
    precio: int = Form(...),
    imagen: UploadFile = File(...)
):
    try:
        print("=== NUEVA SOLICITUD DE SERVICIO ===")
        print(f"Nombre recibido: {nombre}")
        print(f"Precio recibido: {precio}")
        print(f"Archivo recibido: {imagen.filename}, tipo: {imagen.content_type}")
        
        file_bytes = await imagen.read()
        file_name = f"servicio_{int(datetime.now().timestamp())}.{imagen.filename.split('.')[-1]}"
        
        # Intentar subir al Storage
        print("Intentando subir archivo a Supabase Storage...")
        try:
            supabase.storage.from_("fotos-servicios").upload(
                path=file_name,
                file=file_bytes,
                file_options={"content-type": imagen.content_type}
            )
            print("¡Subida a Storage exitosa!")
        except Exception as storage_err:
            print(f"❌ ERROR CRÍTICO EN STORAGE: {storage_err}")
            raise HTTPException(status_code=500, detail=f"Fallo en Storage: {storage_err}")

        # Obtener URL
        imagen_url = supabase.storage.from_("fotos-servicios").get_public_url(file_name)
        print(f"URL generada: {imagen_url}")
        
        # Intentar insertar en la tabla
        print("Intentando insertar registro en la tabla 'servicios'...")
        nuevo_servicio = {"nombre": nombre, "precio": precio, "imagen_url": imagen_url}
        response = supabase.table("servicios").insert(nuevo_servicio).execute()
        print("¡Registro insertado con éxito en la Base de Datos!")
        
        return {"status": "success", "data": response.data}
        
    except Exception as e:
        print(f"❌ ERROR GENERAL EN ENDPOINT: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

        
@app.delete("/api/admin/servicios/{servicio_id}")
def eliminar_servicio(servicio_id: int):
    try:
        supabase.table("servicios").delete().eq("id", servicio_id).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 📅 ENDPOINTS DE CITAS Y BLOQUEOS ---

# Asegúrate de importar datetime y timezone arriba en tu archivo si no están:
# from datetime import datetime, timezone, timedelta

@app.get("/api/horas-disponibles-cliente")
def obtener_horas_cliente(fecha: str):
    try:
        # 1. Buscar qué horas ya están ocupadas o bloqueadas
        fecha_inicio = f"{fecha}T00:00:00Z"
        fecha_fin = f"{fecha}T23:59:59Z"
        
        citas_res = supabase.table("citas") \
            .select("fecha_hora") \
            .gte("fecha_hora", fecha_inicio) \
            .lte("fecha_hora", fecha_fin) \
            .execute()
        
        horas_ocupadas = []
        for registro in citas_res.data:
            dt = datetime.fromisoformat(registro["fecha_hora"].replace("Z", "+00:00"))
            horas_ocupadas.append(dt.strftime("%H:%M"))

        # 2. Filtrar de las horas estándar las que no estén ocupadas
        horas_libres = [hora for hora in HORAS_ESTANDAR if hora not in horas_ocupadas]
        
        # 3. ⏰ NUEVO FILTRO: Eliminar horas pasadas si el cliente consulta el día de HOY
        fecha_actual_local = datetime.now() # Toma la fecha y hora de tu máquina
        fecha_consulta = datetime.strptime(fecha, "%Y-%m-%d")
        
        if fecha_consulta.date() == fecha_actual_local.date():
            hora_actual_str = fecha_actual_local.strftime("%H:%M")
            # Solo dejamos las horas que sean mayores estrictamente a la hora actual
            horas_libres = [hora for hora in horas_libres if hora > hora_actual_str]
        
        return sorted(horas_libres)
    except Exception as e:
        print(f"Error en horas cliente: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reservar", status_code=status.HTTP_201_CREATED)
def reservar_cita(cita: Cita):
    try:
        datos_cita = {"nombre_cliente": cita.nombre_cliente, "whatsapp_cliente": cita.whatsapp_cliente, "servicio": cita.servicio, "fecha_hora": cita.fecha_hora.isoformat(), "estado": "pendiente_abono"}
        response = supabase.table("citas").insert(datos_cita).execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        if "duplicate" in str(e) or "unique" in str(e).lower(): raise HTTPException(status_code=409, detail="Esta hora ya está ocupada.")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/bloqueos")
def obtener_bloqueos_admin(fecha: str):
    try:
        fecha_inicio = f"{fecha}T00:00:00Z"; fecha_fin = f"{fecha}T23:59:59Z"
        citas_res = supabase.table("citas").select("fecha_hora, nombre_cliente").gte("fecha_hora", fecha_inicio).lte("fecha_hora", fecha_fin).execute()
        return citas_res.data
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/gestionar-bloqueo")
def gestionar_bloqueo(data: BloqueoAdmin):
    try:
        fecha_iso = f"{data.fecha}T{data.hora}:00Z"
        if data.bloquear:
            supabase.table("citas").insert({"nombre_cliente": "🚫 BLOQUEADO (ADMIN)", "whatsapp_cliente": "No aplica", "servicio": "Bloqueo manual", "fecha_hora": fecha_iso, "estado": "bloqueado"}).execute()
        else:
            supabase.table("citas").delete().eq("fecha_hora", fecha_iso).execute()
        return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))