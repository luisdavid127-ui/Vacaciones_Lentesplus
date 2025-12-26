import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import date, datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ========================================================
# --- 1. CONFIGURACIÓN DE PÁGINA ---
# ========================================================
st.set_page_config(page_title="Portal Vacaciones", page_icon="👑", layout="wide")
st.markdown("""<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;}</style>""", unsafe_allow_html=True)

ID_CARPETA_DRIVE = "1Xh3NMbD4d-OskTu7cGngWdIgmeZv3lWr"

# ========================================================
# 2. MOTORES DE CALENDARIO
# ========================================================
class CalendarioColombia:
    @staticmethod
    def _calcular_pascua(year):
        a = year % 19; b = year // 100; c = year % 100; d = b // 4; e = b % 4
        f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
        i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31; day = ((h + l - 7 * m + 114) % 31) + 1
        return date(year, month, day)
    @staticmethod
    def obtener_festivos(year):
        feriados = []
        fijos = [(1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25)]
        for m, d in fijos: feriados.append(date(year, m, d))
        emiliani = [(1, 6), (3, 19), (6, 29), (8, 15), (10, 12), (11, 1), (11, 11)]
        for m, d in emiliani:
            dt = date(year, m, d)
            if dt.weekday() != 0: dt += timedelta(days=(7-dt.weekday()))
            feriados.append(dt)
        pascua = CalendarioColombia._calcular_pascua(year)
        feriados.extend([pascua-timedelta(days=3), pascua-timedelta(days=2)])
        feriados.extend([pascua+timedelta(days=43), pascua+timedelta(days=64), pascua+timedelta(days=71)])
        return set(feriados)
    @staticmethod
    def es_dia_habil(fecha, trabaja_sab):
        if fecha.weekday() == 6: return False
        if not trabaja_sab and fecha.weekday() == 5: return False
        return fecha not in CalendarioColombia.obtener_festivos(fecha.year)

class CalendarioArgentina:
    @staticmethod
    def obtener_festivos(year): return {date(year, 1, 1), date(year, 3, 24), date(year, 4, 2), date(year, 5, 1), date(year, 5, 25), date(year, 6, 20), date(year, 7, 9), date(year, 12, 8), date(year, 12, 25)}
    @staticmethod
    def es_dia_habil(f, s): return f.weekday()!=6 and (s or f.weekday()!=5) and f not in CalendarioArgentina.obtener_festivos(f.year)

class CalendarioMexico:
    @staticmethod
    def obtener_festivos(year): return {date(year, 1, 1), date(year, 5, 1), date(year, 9, 16), date(year, 12, 25)}
    @staticmethod
    def es_dia_habil(f, s): return f.weekday()!=6 and (s or f.weekday()!=5) and f not in CalendarioMexico.obtener_festivos(f.year)

class CalendarioChile:
    @staticmethod
    def obtener_festivos(year): return {date(year, 1, 1), date(year, 5, 1), date(year, 9, 18), date(year, 9, 19), date(year, 12, 25)}
    @staticmethod
    def es_dia_habil(f, s): return f.weekday()!=6 and (s or f.weekday()!=5) and f not in CalendarioChile.obtener_festivos(f.year)

class GestorCalendarios:
    @staticmethod
    def calcular_dias_habiles(pais, inicio, fin, trabaja_sab):
        if fin < inicio: return 0, "Fecha final inválida"
        motor = CalendarioColombia
        if pais == "Argentina": motor = CalendarioArgentina
        elif pais == "Mexico": motor = CalendarioMexico
        elif pais == "Chile": motor = CalendarioChile
        
        count = 0
        curr = inicio
        while curr <= fin:
            if motor.es_dia_habil(curr, trabaja_sab): count += 1
            curr += timedelta(days=1)
        return count, None

# ========================================================
# 3. GESTOR DRIVE
# ========================================================
class GestorDrive:
    @staticmethod
    def subir_archivo(archivo_bytes, nombre_archivo):
        if not archivo_bytes: return None
        try:
            creds = service_account.Credentials.from_service_account_info(
                st.secrets["connections"]["gsheets"], scopes=['https://www.googleapis.com/auth/drive']
            )
            service = build('drive', 'v3', credentials=creds)
            file_meta = {'name': nombre_archivo, 'parents': [ID_CARPETA_DRIVE]}
            media = MediaIoBaseUpload(archivo_bytes, mimetype='application/pdf')
            file = service.files().create(body=file_meta, media_body=media, fields='webViewLink').execute()
            return file.get('webViewLink')
        except Exception as e:
            st.error(f"Error Drive: {e}")
            return None

# ========================================================
# 4. CLASES DE DATOS
# ========================================================
class RegistroVacaciones:
    def __init__(self, dias, motivo, tipo, fecha_reg=None, rango=None, estado="APROBADO", archivo=None):
        self.dias_tomados = int(dias)
        self.motivo = motivo
        self.tipo = tipo
        self.fecha_registro = fecha_reg if fecha_reg else date.today().isoformat()
        self.rango = rango
        self.estado = estado
        self.archivo = archivo

class Colaborador:
    def __init__(self, doc, nom, fec, sab, hist=None, pais="Colombia", pwd=None, rol="Colaborador"):
        self.documento = doc
        self.nombre = nom
        self.jornada_sabado = sab
        self.pais = pais
        self.fecha_ingreso = datetime.strptime(fec, "%Y-%m-%d").date() if isinstance(fec, str) else fec
        self.historial = [RegistroVacaciones(**h) if isinstance(h, dict) else h for h in (hist or [])]
        self.password = pwd if pwd else doc
        self.rol = rol

    def agregar_solicitud(self, reg): self.historial.append(reg)
    def cambiar_estado(self, idx, est):
        if 0 <= idx < len(self.historial): self.historial[idx].estado = est; return True
        return False

class CalculadoraVacaciones:
    @staticmethod
    def calcular_dias(fec):
        hoy = date.today()
        meses = (hoy.year - fec.year) * 12 + (hoy.month - fec.month)
        if hoy.day < fec.day: meses -= 1
        return max(0, meses * 1.25)
    @staticmethod
    def saldo(gen, hist):
        gastado = sum(r.dias_tomados for r in hist if r.tipo in ["LEGAL", "DINERO"] and r.estado == "APROBADO")
        return gen - gastado

class CalculadoraBeneficio:
    @staticmethod
    def calcular(fec, hist):
        hoy = date.today()
        if (hoy - fec).days < 365: return 0
        aniv = date(hoy.year if (hoy.month, hoy.day) >= (fec.month, fec.day) else hoy.year - 1, fec.month, fec.day)
        gastado = sum(r.dias_tomados for r in hist if r.tipo == "BENEFICIO" and r.estado == "APROBADO" and datetime.strptime(r.fecha_registro, "%Y-%m-%d").date() >= aniv)
        return max(0, 5 - gastado)

# ========================================================
# 5. SISTEMA PRINCIPAL
# ========================================================
class Sistema:
    def __init__(self):
        try:
            self.conn = st.connection("gsheets", type=GSheetsConnection)
            self.bd = self._cargar()
            self._admin()
        except: self.bd = {}

    def _cargar(self):
        try:
            du = self.conn.read(worksheet="Usuarios", ttl=0)
            dr = self.conn.read(worksheet="Registros", ttl=0)
            
            if du.empty: return {}
            
            du['documento'] = du['documento'].astype(str).str.strip()
            if not dr.empty:
                dr['documento_emp'] = dr['documento_emp'].astype(str).str.strip()
                dr['dias'] = pd.to_numeric(dr['dias'], errors='coerce').fillna(0).astype(int)

            res = {}
            for _, r in du.iterrows():
                doc = r['documento']
                mregs = dr[dr['documento_emp'] == doc] if not dr.empty else pd.DataFrame()
                h_objs = []
                if not mregs.empty:
                    for _, reg in mregs.iterrows():
                        link = reg['archivo'] if 'archivo' in reg and pd.notna(reg['archivo']) else None
                        h_objs.append(RegistroVacaciones(
                            dias=reg['dias'], motivo=reg['motivo'], tipo=reg['tipo'], 
                            fecha_reg=reg['fecha_registro'], rango=reg['rango'], 
                            estado=reg['estado'], archivo=link
                        ))
                
                pais = r['pais'] if 'pais' in r and pd.notna(r['pais']) else "Colombia"
                pwd = str(r['password']) if 'password' in r and pd.notna(r['password']) else str(doc)
                rol = r['rol'] if 'rol' in r and pd.notna(r['rol']) else "Colaborador"
                
                res[doc] = Colaborador(doc, r['nombre'], r['fecha_ingreso'], bool(r['jornada_sabado']), h_objs, pais, pwd, rol)
            return res
        except Exception as e:
            st.error(f"Error cargando: {e}")
            return {}

    def guardar(self):
        try:
            du = []; dr = []
            for d, e in self.bd.items():
                du.append({
                    "documento": str(e.documento), "nombre": e.nombre, "fecha_ingreso": str(e.fecha_ingreso), 
                    "jornada_sabado": e.jornada_sabado, "pais": e.pais, "password": str(e.password), "rol": e.rol
                })
                for r in e.historial:
                    dr.append({
                        "documento_emp": str(e.documento), "dias": int(r.dias_tomados), "motivo": r.motivo, "tipo": r.tipo, 
                        "fecha_registro": r.fecha_registro, "rango": r.rango, "estado": r.estado, "archivo": r.archivo
                    })
            
            if du: self.conn.update(worksheet="Usuarios", data=pd.DataFrame(du))
            
            cols = ["documento_emp", "dias", "motivo", "tipo", "fecha_registro", "rango", "estado", "archivo"]
            df_r = pd.DataFrame(dr) if dr else pd.DataFrame(columns=cols)
            
            self.conn.update(worksheet="Registros", data=df_r)
            st.cache_data.clear()
        except Exception as e: st.error(str(e))

    def _admin(self):
        if "admin" not in self.bd:
            self.bd["admin"] = Colaborador("admin", "Super Admin", "1990-01-01", False, pwd="Lentes2025", rol="Super_Admin")
            self.guardar()

    def _validar_cruce(self, historial, ini_nuevo, fin_nuevo):
        for reg in historial:
            if reg.estado == "RECHAZADO": continue
            try:
                if " al " not in reg.rango: continue 
                partes = reg.rango.split(" al ")
                ini_old = datetime.strptime(partes[0], "%Y-%m-%d").date()
                fin_old = datetime.strptime(partes[1], "%Y-%m-%d").date()
                if ini_nuevo <= fin_old and fin_nuevo >= ini_old: return True
            except: continue
        return False

    def crear_emp(self, d, n, f, s, p, r="Colaborador"):
        if d in self.bd: return False, "Existe"
        self.bd[d] = Colaborador(d, n, f, s, pais=p, pwd=d, rol=r); self.guardar(); return True, "Creado"

    def solicitar(self, uid, ini, fin, mot, ben, file):
        emp = self.bd.get(uid)
        if fin < ini: return False, "Fecha final anterior a inicial"
        if self._validar_cruce(emp.historial, ini, fin): return False, "Cruce de fechas detectado"

        dias, err = GestorCalendarios.calcular_dias_habiles(emp.pais, ini, fin, emp.jornada_sabado)
        if err or dias == 0: return False, err or "0 días hábiles"
        
        if ben:
            disp = CalculadoraBeneficio.calcular(emp.fecha_ingreso, emp.historial)
            if dias > disp: return False, f"Solo tienes {disp} días de beneficio"
        
        link = None
        if file:
            with st.spinner("Subiendo PDF..."):
                link = GestorDrive.subir_archivo(file, f"{uid}_{date.today()}_soporte.pdf")
        
        emp.agregar_solicitud(RegistroVacaciones(dias, mot, "BENEFICIO" if ben else "LEGAL", rango=f"{ini} al {fin}", estado="PENDIENTE", archivo=link))
        self.guardar(); return True, "Solicitud enviada"

    def gestionar(self, d, i, est):
        if self.bd[d].cambiar_estado(i, est): self.guardar(); return True, "OK"
        return False, "Error"

    def pagar(self, d, di, m):
        self.bd[d].agregar_solicitud(RegistroVacaciones(di, m, "DINERO", rango="Pago en Dinero", estado="APROBADO"))
        self.guardar(); return True, "Pagado"

    def editar(self, d, n, f, s, p, r):
        e = self.bd[d]; e.nombre, e.fecha_ingreso, e.jornada_sabado, e.pais, e.rol = n, f, s, p, r
        self.guardar(); return True, "OK"

    def mod_reg(self, d, i, nd, nm):
        e = self.bd[d].historial[i]; e.dias_tomados = int(nd); e.motivo = nm
        self.guardar(); return True, "OK"
    
    def del_reg(self, d, i):
        self.bd[d].historial.pop(i); self.guardar(); return True, "OK"
    
    def del_masivo(self, d, indices):
        for i in sorted(indices, reverse=True): self.bd[d].historial.pop(i)
        self.guardar(); return True, "OK"
    
    def cambiar_pass(self, d, o, n):
        if str(self.bd[d].password) != str(o): return False, "Clave mal"
        self.bd[d].password = n; self.guardar(); return True, "OK"

# ========================================================
# 6. GESTOR DE USUARIOS
# ========================================================
class GestorUsuarios:
    def __init__(self, sistema):
        self.sys = sistema

    def autenticar(self, usuario, password_ingresado):
        if usuario == "admin" and password_ingresado == "Lentes2025": return "Super_Admin", "Super Administrador"
        if usuario in self.sys.bd:
            emp = self.sys.bd[usuario]
            if str(emp.password) == str(password_ingresado): return emp.rol, emp.nombre
        return None, None

    def cambiar_password(self, usuario, actual, nueva):
        if usuario == "admin": return False, "Super Admin no cambia clave aquí."
        emp = self.sys.bd.get(usuario)
        if not emp: return False, "Usuario no encontrado."
        if str(emp.password) != str(actual): return False, "Contraseña incorrecta."
        emp.password = nueva; self.sys.guardar(); return True, "Contraseña actualizada."

    def resetear_password(self, usuario_destino):
        if usuario_destino in self.sys.bd:
            emp = self.sys.bd[usuario_destino]
            emp.password = emp.documento; self.sys.guardar(); return True, f"Clave de {emp.nombre} reseteada."
        return False, "Usuario no encontrado."

# ========================================================
# 7. INTERFAZ GRÁFICA
# ========================================================

if "sys" not in st.session_state: st.session_state.sys = Sistema()
sys = st.session_state.sys
auth = GestorUsuarios(sys)

if "login" not in st.session_state: st.session_state.login = False
if "rol" not in st.session_state: st.session_state.rol = None
if "uid" not in st.session_state: st.session_state.uid = None

def login_ui():
    st.title("🏢 Portal Corporativo Livo Company (Lentesplus)")
    with st.form("log"):
        st.write("### Iniciar Sesión")
        u = st.text_input("Usuario / Documento"); p = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Ingresar"):
            r, n = auth.autenticar(u, p)
            if r:
                st.session_state.login = True; st.session_state.rol = r; st.session_state.uid = u; st.success(f"Bienvenido {n}"); st.rerun()
            else: st.error("Credenciales incorrectas")
    st.caption("Nota: Si es tu primera vez, tu clave es tu documento.")

if not st.session_state.login: login_ui(); st.stop()

# DASHBOARD
rol = st.session_state.rol
uid = st.session_state.uid
me = sys.bd.get(uid)

if not me and uid != "admin": st.error("Usuario no encontrado."); st.session_state.login=False; st.rerun()

nombre_show = "Administrador" if uid == "admin" else me.nombre
st.sidebar.title(f"Hola, {' '.join(nombre_show.split()[:2])}")
st.sidebar.info(f"Perfil: {rol}")
if me and hasattr(me, 'pais'): st.sidebar.info(f"📍 {me.pais}")
if st.sidebar.button("Salir"): st.session_state.login=False; st.rerun()
if st.sidebar.button("🔄 Recargar"): st.cache_data.clear(); st.session_state.sys = Sistema(); st.rerun()

def render_mis_vacaciones(user_id):
    usuario = sys.bd.get(user_id)
    if not usuario: return
    
    st.subheader(f"Vacaciones de {usuario.nombre}")
    gen = CalculadoraVacaciones.calcular_dias(usuario.fecha_ingreso)
    sal = CalculadoraVacaciones.saldo(gen, usuario.historial)
    ben = CalculadoraBeneficio.calcular(usuario.fecha_ingreso, usuario.historial)
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Saldo Legal", int(sal)); c2.metric("Beneficio", ben); c3.metric("Ingreso", str(usuario.fecha_ingreso))
    st.divider()
    
    t1, t2, t3 = st.tabs(["Solicitar", "Historial", "Mi Perfil"])
    
    with t1:
        with st.form(f"req_{user_id}"):
            c1, c2 = st.columns(2); i = c1.date_input("Inicio"); f = c2.date_input("Fin")
            typ = st.radio("Tipo", ["Legal", "Beneficio"]); mot = st.text_input("Motivo", key=f"motivo_solicitud_{user_id}")
            file = st.file_uploader("Soporte PDF", type="pdf")
            if st.form_submit_button("Enviar"):
                ok, m = sys.solicitar(user_id, i, f, mot, typ=="Beneficio", file)
                if ok: st.success(m); st.rerun()
                else: st.error(m)
    with t2:
        if usuario.historial:
            df = [{"Estado": h.estado, "Tipo": h.tipo, "Días": h.dias_tomados, "Fechas": h.rango, "Soporte": "📄" if h.archivo else "-"} for h in usuario.historial]
            st.dataframe(pd.DataFrame(df), use_container_width=True)
        else: st.info("Sin registros")
    with t3:
        st.subheader("Cambiar Contraseña")
        with st.form(f"pass_{user_id}"):
            o = st.text_input("Actual", type="password"); n = st.text_input("Nueva", type="password")
            if st.form_submit_button("Cambiar"):
                ok, m = auth.cambiar_password(user_id, o, n)
                if ok: st.success(m)
                else: st.error(m)

# --- VISTA SEGÚN ROL ---

if rol == "Colaborador":
    st.title("🏖️ Mis Vacaciones")
    render_mis_vacaciones(uid)

elif rol in ["Super_Admin", "Admin"]:
    st.title(f"🏢 Panel Admin")
    t1, t2, t3, t4 = st.tabs(["📬 Inicio", "👥 Gestión", "🛠️ Ajustes", "🏖️ Mis Vacaciones"]) 
    
    with t1:
        pend = False
        for d, e in sys.bd.items():
            if d=="admin": continue
            idxs = [i for i, h in enumerate(e.historial) if h.estado=="PENDIENTE"]
            if idxs:
                pend = True
                with st.expander(f"{e.nombre} ({len(idxs)})", expanded=True):
                    for i in idxs:
                        r = e.historial[i]
                        c1, c2 = st.columns([3,1])
                        lnk = f" [Ver PDF]({r.archivo})" if r.archivo else ""
                        c1.markdown(f"**{r.tipo}** ({r.dias_tomados}d) | {r.rango} {lnk}\n> {r.motivo}")
                        with c2:
                            if st.button("✅", key=f"ok{d}{i}"): sys.gestionar(d, i, "APROBADO"); st.rerun()
                            if st.button("❌", key=f"no{d}{i}"): sys.gestionar(d, i, "RECHAZADO"); st.rerun()
        if not pend: st.success("No hay solicitudes por revisar")

    with t2:
        c1, c2 = st.columns(2)
        with c1:
            st.write("#### Crear Usuario")
            with st.form("new"):
                d = st.text_input("Doc"); n = st.text_input("Nom"); f = st.date_input("Ingreso"); s = st.checkbox("Sábados")
                p = st.selectbox("País", ["Colombia", "Argentina", "Mexico", "Chile"])
                
                r_opts = ["Colaborador"]
                if rol == "Super_Admin": r_opts = ["Colaborador", "Admin", "Super_Admin"]
                elif rol == "Admin": r_opts = ["Colaborador", "Admin"]
                r = st.selectbox("Rol", r_opts)
                
                if st.form_submit_button("Crear"):
                    ok, m = sys.crear_emp(d, n, f, s, p, r)
                    if ok: st.success(m)
                    else: st.error(m)
        with c2:
            st.write("#### Pagar")
            lst = [k for k in sys.bd if k!="admin"]
            if lst:
                sel = st.selectbox("Emp", lst, format_func=lambda x: sys.bd[x].nombre)
                di = st.number_input("Días", 1, step=1); mo = st.text_input("Motivo", key="motivo_pago_admin")
                if st.button("Pagar"): sys.pagar(sel, di, mo); st.success("OK"); st.rerun()

        st.divider()
        st.write("### 📊 Tablero de Control de Vacaciones")
        
        datos_resumen = []
        for doc, emp in sys.bd.items():
            if doc == "admin": continue
            gen = CalculadoraVacaciones.calcular_dias(emp.fecha_ingreso)
            tomados_tiempo = sum(r.dias_tomados for r in emp.historial if r.tipo == "LEGAL" and r.estado == "APROBADO")
            pagados_dinero = sum(r.dias_tomados for r in emp.historial if r.tipo == "DINERO" and r.estado == "APROBADO")
            saldo_legal = gen - tomados_tiempo - pagados_dinero
            saldo_beneficio = CalculadoraBeneficio.calcular(emp.fecha_ingreso, emp.historial)
            
            datos_resumen.append({
                "Documento": doc, "Nombre": emp.nombre, "País": emp.pais,
                "🟢 Disponible Legal": int(saldo_legal), "🏖️ Días Tomados": int(tomados_tiempo),
                "💰 Días Pagados": int(pagados_dinero), "🎁 Beneficio Disp.": int(saldo_beneficio)
            })
        
        if datos_resumen: st.dataframe(pd.DataFrame(datos_resumen), use_container_width=True)
        else: st.info("No hay Colaboradores registrados.")

    with t3:
        lst = [k for k in sys.bd if k!="admin"]
        if lst:
            sel = st.selectbox("Editar", lst, format_func=lambda x: sys.bd[x].nombre, key="ed")
            e = sys.bd[sel]
            cp, ch = st.columns(2)
            with cp:
                with st.form("prof"):
                    nn = st.text_input("Nombre", e.nombre); nf = st.date_input("Fecha de Ingreso", e.fecha_ingreso); ns = st.checkbox("Trabaja los días sábados", e.jornada_sabado)
                    p_list = ["Colombia", "Argentina", "Mexico", "Chile"]
                    try: idx_p = p_list.index(e.pais)
                    except: idx_p = 0
                    np = st.selectbox("País", p_list, index=idx_p)
                    
                    r_opts_ed = ["Colaborador"]
                    bloqueado = False
                    if rol == "Super_Admin": r_opts_ed = ["Colaborador", "Admin", "Super_Admin"]
                    elif rol == "Admin":
                        if e.rol == "Super_Admin": 
                            st.warning("No puedes editar a un Super Admin"); bloqueado = True; r_opts_ed = ["Super_Admin"]
                        else: r_opts_ed = ["Colaborador", "Admin"]
                    
                    try: idx_r = r_opts_ed.index(e.rol)
                    except: idx_r = 0
                    nr = st.selectbox("Rol", r_opts_ed, index=idx_r, disabled=bloqueado)

                    if st.form_submit_button("Guardar"): sys.editar(sel, nn, nf, ns, np, nr); st.success("OK"); st.rerun()
            with ch:
                if e.historial:
                    st.dataframe(pd.DataFrame([{"ID": i, "Estado solicitud": h.estado, "Días solicitados": h.dias_tomados} for i, h in enumerate(e.historial)]), use_container_width=True)
                    with st.expander("Modificar Registro Individual"):
                        idt = st.number_input("ID", 0, len(e.historial)-1, step=1)
                        if 0<=idt<len(e.historial):
                            nd = st.number_input("Días solicitados", value=int(e.historial[idt].dias_tomados), step=1)
                            nm = st.text_input("Motivo", value=e.historial[idt].motivo, key=f"motivo_edit_{sel}_{idt}")
                            if st.button("Guardar"): sys.mod_reg(sel, idt, nd, nm); st.success("OK"); st.rerun()
                    
                    if rol == "Super_Admin":
                        st.divider()
                        st.write("#### 🗑️ Borrado Masivo")
                        ids_del = st.multiselect("Seleccionar IDs", [i for i in range(len(e.historial))])
                        if ids_del:
                            if st.button("CONFIRMAR BORRADO", type="primary"):
                                sys.del_masivo(sel, ids_del)
                                st.success("Eliminados"); st.rerun()
                    else: st.caption("🔒 Solo Super_Admin borra")
                
                if rol == "Super_Admin":
                    st.divider()
                    if st.button(f"Resetear Clave de {e.nombre}"):
                        ok, m = auth.resetear_password(sel)
                        if ok: st.success(m)

    with t4: 
        render_mis_vacaciones(uid)


