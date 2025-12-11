import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import date, datetime, timedelta

# --- 1. CONFIGURACIÓN DE PÁGINA (SIEMPRE AL INICIO) ---
st.set_page_config(page_title="Portal Vacaciones", page_icon="🏢", layout="wide")

# Ocultar menú de desarrollador
st.markdown("""<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;}</style>""", unsafe_allow_html=True)

# ========================================================
# 2. CLASES DE NEGOCIO
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
    def _mover_a_lunes(fecha_festivo):
        if fecha_festivo.weekday() == 0: return fecha_festivo
        days_ahead = 7 - fecha_festivo.weekday()
        return fecha_festivo + timedelta(days=days_ahead)
    @staticmethod
    def obtener_festivos(year):
        festivos = []
        fijos = [(1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25)]
        for mes, dia in fijos: festivos.append(date(year, mes, dia))
        emiliani = [(1, 6), (3, 19), (6, 29), (8, 15), (10, 12), (11, 1), (11, 11)]
        for mes, dia in emiliani: festivos.append(CalendarioColombia._mover_a_lunes(date(year, mes, dia)))
        pascua = CalendarioColombia._calcular_pascua(year)
        festivos.append(pascua - timedelta(days=3)); festivos.append(pascua - timedelta(days=2))
        festivos.append(CalendarioColombia._mover_a_lunes(pascua + timedelta(days=39)))
        festivos.append(CalendarioColombia._mover_a_lunes(pascua + timedelta(days=60)))
        festivos.append(CalendarioColombia._mover_a_lunes(pascua + timedelta(days=68)))
        return set(festivos)
    @staticmethod
    def es_dia_habil(fecha_obj, trabaja_sabados):
        if fecha_obj.weekday() == 6: return False
        if not trabaja_sabados and fecha_obj.weekday() == 5: return False
        festivos_anio = CalendarioColombia.obtener_festivos(fecha_obj.year)
        if fecha_obj in festivos_anio: return False
        return True
    @staticmethod
    def calcular_dias_vacaciones(fecha_inicio, fecha_fin, trabaja_sabados):
        if fecha_fin < fecha_inicio: return 0, "La fecha final es anterior a la inicial."
        contador = 0; actual = fecha_inicio
        while actual <= fecha_fin:
            if CalendarioColombia.es_dia_habil(actual, trabaja_sabados): contador += 1
            actual += timedelta(days=1)
        return contador, None

class RegistroVacaciones:
    def __init__(self, dias_tomados, motivo, tipo="LEGAL", fecha_registro=None, rango=None, estado="APROBADO"):
        self.dias_tomados = int(dias_tomados) # ENTERO
        self.motivo = motivo
        self.tipo = tipo
        self.fecha_registro = fecha_registro if fecha_registro else date.today().isoformat()
        self.rango = rango
        self.estado = estado 
    def to_dict(self): return {"dias_tomados": self.dias_tomados, "motivo": self.motivo, "tipo": self.tipo, "fecha_registro": self.fecha_registro, "rango": self.rango, "estado": self.estado}

class Empleado:
    def __init__(self, documento, nombre, fecha_ingreso, jornada_sabado=False, historial=None):
        self.documento = documento
        self.nombre = nombre
        self.jornada_sabado = jornada_sabado
        self.fecha_ingreso = datetime.strptime(fecha_ingreso, "%Y-%m-%d").date() if isinstance(fecha_ingreso, str) else fecha_ingreso
        self.historial = [RegistroVacaciones(**h) for h in historial] if historial else []
    def agregar_solicitud(self, reg): self.historial.append(reg)
    def cambiar_estado_solicitud(self, idx, nuevo_estado):
        if 0 <= idx < len(self.historial): self.historial[idx].estado = nuevo_estado; return True
        return False
    def to_dict(self): return {"documento": self.documento, "nombre": self.nombre, "fecha_ingreso": self.fecha_ingreso.isoformat(), "jornada_sabado": self.jornada_sabado, "historial": [r.to_dict() for r in self.historial]}

class CalculadoraVacaciones:
    @staticmethod
    def calcular_dias_generados(fecha_ingreso):
        hoy = date.today()
        meses = (hoy.year - fecha_ingreso.year) * 12 + (hoy.month - fecha_ingreso.month)
        if hoy.day < fecha_ingreso.day: meses -= 1
        return max(0, meses * 1.25)
    @staticmethod
    def calcular_saldo(generados, historial):
        gastados = sum(r.dias_tomados for r in historial if r.tipo in ["LEGAL", "DINERO"] and r.estado == "APROBADO")
        return generados - gastados

class CalculadoraBeneficio:
    @staticmethod
    def calcular(fecha_ingreso, historial):
        hoy = date.today()
        if (hoy - fecha_ingreso).days < 365: return 0, "Requiere 1 año antigüedad."
        aniv_year = hoy.year
        if (hoy.month, hoy.day) < (fecha_ingreso.month, fecha_ingreso.day): aniv_year -= 1
        inicio_periodo = date(aniv_year, fecha_ingreso.month, fecha_ingreso.day)
        gastados = sum(r.dias_tomados for r in historial if r.tipo == "BENEFICIO" and r.estado == "APROBADO" and datetime.strptime(r.fecha_registro, "%Y-%m-%d").date() >= inicio_periodo)
        return max(0, 5 - gastados), f"Ciclo {inicio_periodo}"

class Validador:
    @staticmethod
    def validar(dias, saldo, fecha_ingreso):
        if (date.today() - fecha_ingreso).days < 365: return False, "Falta antigüedad."
        if dias <= 0: return False, "Días deben ser positivos."
        if dias > saldo: return False, f"Saldo insuficiente ({saldo:.2f})."
        return True, ""

# --- CLASE SISTEMA CONECTADA A GOOGLE SHEETS ---
class Sistema:
    def __init__(self):
        try:
            self.conn = st.connection("gsheets", type=GSheetsConnection)
            self.bd = self._cargar_desde_sheets()
            self._asegurar_admin()
        except Exception as e:
            st.error(f"Error de conexión con Google Sheets: {e}. Verifica secrets.toml")
            self.bd = {}

    def _cargar_desde_sheets(self):
        try:
            df_users = self.conn.read(worksheet="Usuarios", ttl=0)
            df_regs = self.conn.read(worksheet="Registros", ttl=0)
            if df_users.empty: return {}
            resultados = {}
            for _, row in df_users.iterrows():
                doc = str(row['documento'])
                mis_regs = df_regs[df_regs['documento_emp'].astype(str) == doc] if not df_regs.empty else pd.DataFrame()
                historial_objs = []
                if not mis_regs.empty:
                    for _, reg in mis_regs.iterrows():
                        historial_objs.append({
                            "dias_tomados": reg['dias'], "motivo": reg['motivo'], "tipo": reg['tipo'],
                            "fecha_registro": reg['fecha_registro'], "rango": reg['rango'], "estado": reg['estado'],
                            "archivo": reg['archivo'] if pd.notna(reg['archivo']) else None
                        })
                resultados[doc] = Empleado(documento=doc, nombre=row['nombre'], fecha_ingreso=row['fecha_ingreso'], jornada_sabado=bool(row['jornada_sabado']), historial=historial_objs)
            return resultados
        except Exception: return {}

    def guardar(self):
        try:
            data_users = []
            data_regs = []
            for doc, emp in self.bd.items():
                data_users.append({"documento": str(emp.documento), "nombre": emp.nombre, "fecha_ingreso": str(emp.fecha_ingreso), "jornada_sabado": emp.jornada_sabado})
                for reg in emp.historial:
                    data_regs.append({"documento_emp": str(emp.documento), "dias": int(reg.dias_tomados), "motivo": reg.motivo, "tipo": reg.tipo, "fecha_registro": reg.fecha_registro, "rango": reg.rango, "estado": reg.estado, "archivo": str(reg.archivo) if reg.archivo else ""})
            
            if data_users: self.conn.update(worksheet="Usuarios", data=pd.DataFrame(data_users))
            df_regs = pd.DataFrame(data_regs) if data_regs else pd.DataFrame(columns=["documento_emp", "dias", "motivo", "tipo", "fecha_registro", "rango", "estado", "archivo"])
            self.conn.update(worksheet="Registros", data=df_regs)
            st.cache_data.clear()
        except Exception as e: st.error(f"Error al guardar: {e}")

    def _asegurar_admin(self):
        if "admin" not in self.bd:
            self.bd["admin"] = Empleado("admin", "Super Admin", "2000-01-01", False)
            self.guardar()

    def solicitar_vacaciones(self, doc, ini, fin, motivo, es_ben):
        emp = self.bd.get(doc)
        dias, err = CalendarioColombia.calcular_dias_vacaciones(ini, fin, emp.jornada_sabado)
        if err: return False, err
        if dias == 0: return False, "Rango sin días hábiles."
        if es_ben:
            disp, msg = CalculadoraBeneficio.calcular(emp.fecha_ingreso, emp.historial)
            if dias > disp: return False, f"Solo tienes {disp} días."
            tipo = "BENEFICIO"
        else:
            gen = CalculadoraVacaciones.calcular_dias_generados(emp.fecha_ingreso)
            saldo = CalculadoraVacaciones.calcular_saldo(gen, emp.historial)
            ok, msg = Validador.validar(dias, saldo, emp.fecha_ingreso)
            if not ok: return False, msg
            tipo = "LEGAL"
        emp.agregar_solicitud(RegistroVacaciones(int(dias), motivo, tipo, rango=f"{ini} al {fin}", estado="PENDIENTE"))
        self.guardar(); return True, "Enviado a aprobación."

    def gestionar_solicitud(self, doc_empleado, idx_solicitud, accion):
        emp = self.bd.get(doc_empleado)
        if emp and emp.cambiar_estado_solicitud(idx_solicitud, accion):
            self.guardar(); return True, f"Solicitud {accion}."
        return False, "Error."
    def crear_empleado(self, doc, nom, fec, trabaja_sab):
        if doc in self.bd: return False, "Ya existe."
        self.bd[doc] = Empleado(doc, nom, fec, trabaja_sab)
        self.guardar(); return True, "Creado."
    def procesar_dinero(self, doc, dias, motivo):
        emp = self.bd.get(doc)
        gen = CalculadoraVacaciones.calcular_dias_generados(emp.fecha_ingreso)
        saldo = CalculadoraVacaciones.calcular_saldo(gen, emp.historial)
        ok, msg = Validador.validar(dias, saldo, emp.fecha_ingreso)
        if not ok: return False, msg
        emp.agregar_solicitud(RegistroVacaciones(int(dias), motivo, "DINERO", rango="Compensación", estado="APROBADO"))
        self.guardar(); return True, "Compensado."
    def editar_perfil(self, doc, n_nom, n_fec, n_jor):
        emp = self.bd.get(doc)
        emp.nombre, emp.fecha_ingreso, emp.jornada_sabado = n_nom, n_fec, n_jor
        self.guardar(); return True, "Actualizado."
    def editar_registro(self, doc, idx, n_dias, n_mot):
        emp = self.bd.get(doc)
        if emp and 0 <= idx < len(emp.historial):
            emp.historial[idx].dias_tomados = int(n_dias)
            emp.historial[idx].motivo = n_mot
            self.guardar(); return True, "Actualizado."
        return False, "Error."
    def eliminar_registro(self, doc, idx):
        emp = self.bd.get(doc)
        if emp and 0 <= idx < len(emp.historial):
            emp.historial.pop(idx)
            self.guardar(); return True, "Eliminado."
        return False, "Error."

# ========================================================
# 3. GESTIÓN DE ESTADO Y LOGIN
# ========================================================

sistema = Sistema()

if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "user_role" not in st.session_state: st.session_state.user_role = None
if "user_id" not in st.session_state: st.session_state.user_id = None

def login_screen():
    st.title("🏢 Portal Corporativo")
    t1, t2 = st.tabs(["👤 Empleado", "🔒 Admin"])
    with t1:
        doc = st.text_input("Documento de Identidad")
        if st.button("Entrar Empleado"):
            if doc in sistema.bd and doc != "admin":
                st.session_state.logged_in = True
                st.session_state.user_role = "Empleado"
                st.session_state.user_id = doc
                st.rerun()
            else: st.error("No encontrado.")
    with t2:
        u = st.text_input("Usuario")
        p = st.text_input("Clave", type="password")
        if st.button("Entrar Admin"):
            if u == "admin" and p == "Lentes2025":
                st.session_state.logged_in = True
                st.session_state.user_role = "Admin"
                st.session_state.user_id = "admin"
                st.rerun()
            else: st.error("Error.")

def logout():
    st.session_state.logged_in = False; st.session_state.user_role = None; st.rerun()

if not st.session_state.logged_in:
    login_screen(); st.stop()

# ========================================================
# 4. APLICACIÓN PRINCIPAL
# ========================================================

rol = st.session_state.user_role
uid = st.session_state.user_id
nombre_usr = "Administrador" if rol == "Admin" else sistema.bd[uid].nombre

st.sidebar.title(f"Hola, {' '.join(nombre_usr.split()[:2])}")
st.sidebar.info(f"Perfil: {rol}")
if st.sidebar.button("Cerrar Sesión"): logout()

# --- VISTA EMPLEADO ---
if rol == "Empleado":
    st.title("🏖️ Mis Vacaciones")
    emp = sistema.bd[uid]
    gen = CalculadoraVacaciones.calcular_dias_generados(emp.fecha_ingreso)
    saldo_ley = CalculadoraVacaciones.calcular_saldo(gen, emp.historial)
    saldo_ben, _ = CalculadoraBeneficio.calcular(emp.fecha_ingreso, emp.historial)
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Vacaciones Disponibles", int(saldo_ley))
    c2.metric("Beneficio Disponible", saldo_ben)
    c3.metric("Fecha de ingreso", str(emp.fecha_ingreso))
    st.divider() 
    
    st.subheader("📅 Solicitar")
    with st.form("solicitud"):
        c1, c2 = st.columns(2)
        ini = c1.date_input("Desde")
        fin = c2.date_input("Hasta")
        tipo = st.radio("Tipo", ["Vacaciones de Ley", "Beneficio 5 Días"])
        motivo = st.text_input("Motivo")
        if st.form_submit_button("Enviar"):
            es_ben = (tipo == "Beneficio 5 Días")
            ok, msg = sistema.solicitar_vacaciones(uid, ini, fin, motivo, es_ben)
            if ok: st.success(msg); st.rerun()
            else: st.error(msg)
    
    st.subheader("📜 Historial")
    if emp.historial:
        data = []
        for h in emp.historial:
            if h.estado == "APROBADO": icon = "✅ APROBADO"
            elif h.estado == "PENDIENTE": icon = "⏳ PENDIENTE"
            else: icon = "❌ RECHAZADO"
            data.append({"Estado": icon, "Tipo": h.tipo, "Días": h.dias_tomados, "Fechas": h.rango, "Motivo": h.motivo})
        st.dataframe(pd.DataFrame(data), use_container_width=True)
    else: st.info("Sin registros.")

# --- VISTA ADMIN ---
else:
    st.title("🏢 Panel RRHH")
    t1, t2, t3 = st.tabs(["📬 Buzón", "👥 Empleados", "🛠️ Mantenimiento"])
    
    with t1: 
        pendientes = False
        for doc, emp in sistema.bd.items():
            if doc == "admin": continue
            idxs = [i for i, h in enumerate(emp.historial) if h.estado == "PENDIENTE"]
            if idxs:
                pendientes = True
                with st.expander(f"{emp.nombre} ({len(idxs)})", expanded=True):
                    for idx in idxs:
                        reg = emp.historial[idx]
                        c1, c2 = st.columns([3,1])
                        c1.write(f"**{reg.tipo}** ({reg.dias_tomados} días) | {reg.rango}\n> {reg.motivo}")
                        with c2:
                            if st.button("✅", key=f"ok_{doc}_{idx}"): sistema.gestionar_solicitud(doc, idx, "APROBADO"); st.rerun()
                            if st.button("❌", key=f"no_{doc}_{idx}"): sistema.gestionar_solicitud(doc, idx, "RECHAZADO"); st.rerun()
        if not pendientes: st.success("Todo al día.")

    with t2:
        c1, c2 = st.columns(2)
        with c1:
            st.write("#### Crear Empleado")
            with st.form("new_emp"):
                d = st.text_input("Documento")
                n = st.text_input("Nombre")
                f = st.date_input("Ingreso")
                s = st.checkbox("Sábados")
                if st.form_submit_button("Crear"):
                    ok, m = sistema.crear_empleado(d, n, f, s)
                    if ok: st.success(m)
                    else: st.error(m)
        with c2:
            st.write("#### Pagar Dinero")
            lst = [k for k in sistema.bd.keys() if k != "admin"]
            if lst:
                sel = st.selectbox("Empleado", lst, format_func=lambda x: f"{x} - {sistema.bd[x].nombre}")
                dias = st.number_input("Días", min_value=1, step=1)
                mot = st.text_input("Motivo")
                if st.button("Pagar"):
                    ok, m = sistema.procesar_dinero(sel, dias, mot)
                    if ok: st.success(m)
                    else: st.error(m)
    
    with t3:
        st.subheader("🔧 Corrección")
        lst = [k for k in sistema.bd.keys() if k != "admin"]
        if lst:
            sel = st.selectbox("Editar a:", lst, format_func=lambda x: f"{x} - {sistema.bd[x].nombre}", key="ed_sel")
            emp = sistema.bd[sel]
            st.divider()
            c_pf, c_hs = st.columns(2)
            with c_pf:
                with st.form("ed_prof"):
                    nn = st.text_input("Nombre", value=emp.nombre)
                    nf = st.date_input("Ingreso", value=emp.fecha_ingreso)
                    ns = st.checkbox("Sábados", value=emp.jornada_sabado)
                    if st.form_submit_button("Actualizar"): sistema.editar_perfil(sel, nn, nf, ns); st.success("OK"); st.rerun()
            with c_hs:
                if emp.historial:
                    data = [{"ID": i, "Est": h.estado, "Días": h.dias_tomados, "Motivo": h.motivo} for i, h in enumerate(emp.historial)]
                    st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
                    with st.expander("Modificar"):
                        idt = st.number_input("ID", min_value=0, max_value=len(emp.historial)-1, step=1)
                        if 0 <= idt < len(emp.historial):
                            nd = st.number_input("Días", value=int(emp.historial[idt].dias_tomados), min_value=0, step=1)
                            nm = st.text_input("Motivo", value=emp.historial[idt].motivo)
                            if st.button("Guardar"): sistema.editar_registro(sel, idt, nd, nm); st.success("OK"); st.rerun()
                            if st.button("Eliminar", type="primary"): sistema.eliminar_registro(sel, idt); st.warning("Borrado"); st.rerun()
                else: st.info("Sin historial.")