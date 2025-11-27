import streamlit as st
from streamlit_option_menu import option_menu
from streamlit_quill import st_quill
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import openai
import hashlib
import datetime
import io
import time


st.set_page_config(page_title="WikiMovil 3", page_icon="📡", layout="wide")

# CSS PARA OCULTAR MARCAS DE STREAMLIT Y AJUSTAR LOGIN
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            /* Esto sube todo el contenido para que no haya espacio blanco arriba */
            .block-container {
                padding-top: 1rem;
                padding-bottom: 0rem;
            }
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# CONFIGURACIÓN DE LA PÁGINA Y ESTILOS
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="WikiMovil 3",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos CSS 
st.markdown("""
<style>
    /* Ajustes generales */
    .main .block-container { padding-top: 2rem; }
    
    /* Header Usuario Flotante */
    .user-header {
        position: fixed;
        top: 0.5rem;
        right: 1rem;
        z-index: 9999;
        display: flex;
        align-items: center;
        background-color: #f0f2f6;
        padding: 5px 15px;
        border-radius: 20px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .user-avatar {
        width: 35px;
        height: 35px;
        border-radius: 50%;
        background-color: #4285F4;
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        margin-right: 10px;
    }
    .user-name {
        font-family: sans-serif;
        font-size: 0.9rem;
        color: #333;
    }

    /* Estilo Feed (Cards) */
    .post-card {
        background-color: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.12);
        margin-bottom: 20px;
        border: 1px solid #e0e0e0;
    }
    .post-header { display: flex; justify-content: space-between; margin-bottom: 10px; color: #666; font-size: 0.8em;}
    .post-author { font-weight: bold; color: #000; }
    .post-tags { margin-top: 10px; }
    .tag-badge { 
        background-color: #e8f0fe; 
        color: #1967d2; 
        padding: 2px 8px; 
        border-radius: 12px; 
        font-size: 0.75em; 
        margin-right: 5px;
        display: inline-block;
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# CONEXIÓN Y SERVICIOS GOOGLE (Sheets & Drive)
# -----------------------------------------------------------------------------

# Scope necesario para Drive y Sheets
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file"
]

def get_google_creds():
    """Obtiene credenciales desde st.secrets"""
    if "gcp_service_account" not in st.secrets:
        st.error("Faltan las credenciales en .streamlit/secrets.toml")
        st.stop()
    
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return creds

def get_db_connection():
    """Conecta a Google Sheets y devuelve los worksheets"""
    creds = get_google_creds()
    client = gspread.authorize(creds)
    
    # Abrir Spreadsheet por nombre (Definido en secrets o hardcodeado)
    sheet_name = st.secrets.get("SHEET_NAME", "WikiMovil3_DB")
    try:
        sh = client.open(sheet_name)
    except Exception as e:
        st.error(f"No se pudo abrir la hoja de cálculo '{sheet_name}'. Error: {e}")
        st.stop()
        
    return {
        "users": sh.worksheet("users"),
        "posts": sh.worksheet("posts"),
        "config": sh.worksheet("config")
    }

def upload_image_to_drive(file_obj):
    """Sube imagen a Google Drive y retorna el link público"""
    try:
        creds = get_google_creds()
        service = build('drive', 'v3', credentials=creds)
        
        folder_id = st.secrets.get("DRIVE_FOLDER_ID")
        if not folder_id:
            st.warning("DRIVE_FOLDER_ID no configurado. Imagen no subida.")
            return None

        file_metadata = {
            'name': f"img_{int(time.time())}_{file_obj.name}",
            'parents': [folder_id]
        }
        
        # Streamlit devuelve un BytesIO
        media = MediaIoBaseUpload(file_obj, mimetype=file_obj.type, resumable=True)
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webContentLink'
        ).execute()
        
        file_id = file.get('id')
        
        # HACER PÚBLICO EL ARCHIVO 
        permission = {
            'type': 'anyone',
            'role': 'reader',
        }
        service.permissions().create(
            fileId=file_id,
            body=permission,
            fields='id',
        ).execute()

        return file.get('webContentLink')

    except Exception as e:
        st.error(f"Error subiendo imagen a Drive: {e}")
        return None

# -----------------------------------------------------------------------------
# FUNCIONES DE UTILIDAD (Auth, Hashing, OpenAI)
# -----------------------------------------------------------------------------

def make_hash(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_login(username, password, db):
    try:
        users_df = pd.DataFrame(db["users"].get_all_records())
        if users_df.empty:
            return False, None
            
        user = users_df[users_df['user'] == username]
        if not user.empty:
            stored_hash = user.iloc[0]['pass_hash']
            if make_hash(password) == stored_hash:
                return True, user.iloc[0]
        return False, None
    except Exception as e:
        st.error(f"Error de lectura DB: {e}")
        return False, None
    
def create_user(username, name, password, email, db):
    # 1. Verificar si el usuario ya existe
    users_sheet = db["users"]
    users_data = users_sheet.get_all_records()
    df_users = pd.DataFrame(users_data)
    
    if not df_users.empty and username in df_users['user'].values:
        raise ValueError("El usuario ya existe.")
    
    # 2. Hashear la contraseña (asegúrate de tener la funcion make_hash)
    pass_hash = make_hash(password)
    
    # 3. Agregar a Google Sheets
    # El orden debe coincidir con tus columnas: user, name, pass_hash, email, role
    new_row = [username, name, pass_hash, email, "user"] 
    users_sheet.append_row(new_row)

def get_hashtags(db):
    """Obtiene hashtags estáticos + dinámicos de la DB"""
    static_tags = [
        "Datos Utiles", "Pases a FO", "Tarea Soporte", "reclamos energia", 
        "Reclamos Edenor/Edesur/Coop.", "Reclamos Movistar", "Reclamos Claro", 
        "Reclamos Satelital", "Reclamos Cycsa/AM Tower", "Reclamos Aubasa/ITTel", 
        "Reclamos Metrotel", "Reclamos Torresec", "Reclamos Ufinet", 
        "Pedidos de Repuesto", "salesforce", "BMC Helix"
    ]
    
    try:
        config_df = pd.DataFrame(db["config"].get_all_records())
        if not config_df.empty:
            dynamic_tags = config_df[config_df['key'] == 'hashtag']['value'].tolist()
            static_tags.extend(dynamic_tags)
    except:
        pass # Si falla, usa solo los estáticos
    
    return sorted(list(set(static_tags)))

def add_new_hashtag(db, new_tag):
    try:
        db["config"].append_row(["hashtag", new_tag])
    except Exception as e:
        st.warning(f"No se pudo guardar el nuevo tag: {e}")

def get_openai_response(query, context_text):
    if "openai_api_key" not in st.secrets:
        return "Error: API Key de OpenAI no configurada."
    
    openai.api_key = st.secrets["openai_api_key"]
    
    messages = [
        {"role": "system", "content": "Eres un asistente experto para técnicos de telecomunicaciones. Usa el siguiente contexto (posts de la red social) para responder. Si no sabes, di que no tienes información."},
        {"role": "user", "content": f"Contexto:\n{context_text}\n\nPregunta: {query}"}
    ]
    
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error IA: {e}"

# -----------------------------------------------------------------------------
# COMPONENTES DE UI
# -----------------------------------------------------------------------------

def render_user_header():
    if st.session_state.get("logged_in"):
        user = st.session_state["user_info"]
        initials = user['user'][:2].upper()
        name = user['user']
        st.markdown(f"""
        <div class="user-header">
            <div class="user-avatar">{initials}</div>
            <div class="user-name">{name}</div>
        </div>
        """, unsafe_allow_html=True)


def login_page(db):
    st.markdown("<h1 style='text-align: center;'>📡 WikiMovil 3</h1>", unsafe_allow_html=True)
    
    
    if 'account_created' not in st.session_state:
        st.session_state['account_created'] = False

    
    tab1, tab2, tab3 = st.tabs(["Iniciar Sesión", "Registrarse", "Recuperar Contraseña"])

    # --- TAB 1: LOGIN ---
    with tab1:
        # Si el usuario viene de registrarse, le mostramos éxito aquí
        if st.session_state['account_created']:
            st.success("✅ ¡Cuenta creada con éxito! Por favor ingresa tus datos abajo.")
            # Reiniciamos la variable para que no salga el mensaje eternamente
            st.session_state['account_created'] = False

        with st.form("login_form"):
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Entrar", use_container_width=True)
            
            if submitted:
                
                is_valid, user_data = check_login(username, password, db)
                
                if is_valid:
                    st.session_state['authenticated'] = True
                    st.session_state['user'] = user_data['user']
                    st.session_state['role'] = user_data['role']
                    # .get() por seguridad si la columna name no existe
                    st.session_state['name'] = user_data.get('name', user_data['user']) 
                    st.success(f"¡Bienvenido {user_data['user']}!")
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos. (Verifica los nombres de columna en tu Excel)")

    # --- TAB 2: REGISTRO ---
    with tab2:
        with st.form("register_form"):
            st.write("Crea tu cuenta operativa")
            new_user = st.text_input("Usuario (Ej: jperalta)")
            new_name = st.text_input("Nombre Completo")
            new_email = st.text_input("Email Corporativo")
            new_pass = st.text_input("Contraseña", type="password")
            new_pass2 = st.text_input("Repetir Contraseña", type="password")
            
            reg_submitted = st.form_submit_button("Crear Cuenta", use_container_width=True)
            
            if reg_submitted:
                if new_pass != new_pass2:
                    st.error("Las contraseñas no coinciden.")
                elif len(new_pass) < 6:
                    st.error("La contraseña debe tener al menos 6 caracteres.")
                elif new_user == "" or new_email == "":
                    st.error("Todos los campos son obligatorios.")
                else:
                    try:
                        create_user(new_user, new_name, new_pass, new_email, db)
                        st.success("¡Cuenta creada! Redirigiendo al login...")
                        st.session_state['account_created'] = True
                        import time
                        time.sleep(1) # Pausa dramática de 1 segundo
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al crear usuario: {e}")

    # --- TAB 3: RECUPERAR ---
    with tab3:
        st.info("Sistema de recuperación vía Email")
        rec_email = st.text_input("Ingresa tu email registrado")
        if st.button("Enviar contraseña provisoria"):
            st.warning("Contacta al administrador para resetear tu clave por ahora.")

def feed_view(db, filter_tag=None):
    st.subheader("📰 Últimas Actualizaciones" if not filter_tag else f"🔍 Resultados para: {filter_tag}")
    
    try:
        posts_data = db["posts"].get_all_records()
        df = pd.DataFrame(posts_data)
        
        if df.empty:
            st.info("No hay publicaciones aún.")
            return

        # Ordenar por fecha desc
        df = df.sort_values(by="id", ascending=False)
        
        if filter_tag:
            # Filtrar si el tag está en la cadena de hashtags
            df = df[df['hashtags'].astype(str).str.contains(filter_tag, case=False, na=False)]

        for index, row in df.iterrows():
            with st.container():
                st.markdown(f"""
                <div class="post-card">
                    <div class="post-header">
                        <span class="post-author">@{row['autor']}</span>
                        <span>{row['fecha']}</span>
                    </div>
                """, unsafe_allow_html=True)
                
                # Contenido HTML Seguro (Rich Text)
                st.markdown(row['contenido_html'], unsafe_allow_html=True)
                
                # Imagen
                if row['image_url'] and str(row['image_url']).strip() != "":
                    st.image(row['image_url'], use_container_width=True)
                
                # Tags
                tags = str(row['hashtags']).split(",")
                tags_html = "".join([f'<span class="tag-badge">#{t.strip()}</span>' for t in tags if t.strip()])
                st.markdown(f'<div class="post-tags">{tags_html}</div>', unsafe_allow_html=True)
                
                st.markdown("</div>", unsafe_allow_html=True)
                
    except Exception as e:
        st.error(f"Error cargando feed: {e}")

def new_post_page(db):
    st.header("➕ Nuevo Post")
    
    # Editor Quill
    content = st_quill(
        placeholder="Escribe aquí los detalles técnicos...",
        html=True,
        key="quill_editor",
        toolbar=[
            ['bold', 'italic', 'underline'],
            [{'color': []}, {'background': []}],
            [{'header': [1, 2, 3, False]}],
            ['clean']
        ]
    )
    
    # Imagen
    uploaded_file = st.file_uploader("Adjuntar Imagen (Opcional)", type=['png', 'jpg', 'jpeg'])
    
    # Hashtags
    all_tags = get_hashtags(db)
    selected_tags = st.multiselect("Etiquetas (Obligatorio)", all_tags)
    
    # Nuevo Tag
    create_new_tag = st.checkbox("¿Nuevo Tag?")
    new_tag_val = ""
    if create_new_tag:
        new_tag_val = st.text_input("Escribe el nuevo tag:")
    
    if st.button("Publicar Post", type="primary"):
        if not content:
            st.warning("El contenido no puede estar vacío.")
            return
        if not selected_tags and not new_tag_val:
            st.warning("Selecciona al menos un hashtag.")
            return
            
        with st.spinner("Publicando..."):
            # Subir Imagen si existe
            image_url = ""
            if uploaded_file:
                link = upload_image_to_drive(uploaded_file)
                if link:
                    image_url = link
            
            # Gestionar tags
            final_tags = selected_tags
            if new_tag_val:
                final_tags.append(new_tag_val)
                add_new_hashtag(db, new_tag_val)
            
            tags_str = ",".join(final_tags)
            
            # Guardar en Sheets
            new_id = int(time.time())
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            author = st.session_state["user_info"]["user"]
            
            row = [new_id, timestamp, author, content, image_url, tags_str, 0]
            db["posts"].append_row(row)
            
            st.success("¡Post publicado con éxito!")
            time.sleep(1)
            st.rerun()

def explore_page(db):
    st.header("🔍 Explorar Conocimiento")
    all_tags = get_hashtags(db)
    
    # Nube de tags simulada con botones
    st.markdown("##### Filtrar por tema:")
    cols = st.columns(4)
    selected_filter = None
    
    for i, tag in enumerate(all_tags):
        with cols[i % 4]:
            if st.button(f"#{tag}", key=f"btn_{tag}", use_container_width=True):
                st.session_state["explore_filter"] = tag
                st.rerun()
    
    st.divider()
    
    current_filter = st.session_state.get("explore_filter", None)
    if st.button("Limpiar Filtros", type="secondary"):
        st.session_state["explore_filter"] = None
        st.rerun()
        
    feed_view(db, current_filter)

def chat_ai_page(db):
    st.header("🤖 Asistente Técnico IA")
    st.info("Pregunta sobre procedimientos, pases a FO o reclamos históricos.")
    
    query = st.text_input("¿En qué puedo ayudarte?")
    if st.button("Consultar"):
        with st.spinner("Analizando base de conocimientos..."):
            # Obtener contexto 
            try:
                posts = db["posts"].get_all_records()
                df = pd.DataFrame(posts)
                if not df.empty:
                    # Crear texto de contexto concatenando posts relevantes
                    
                    df['text_repr'] = df['fecha'] + " - " + df['hashtags'] + ": " + df['contenido_html']
                    context = "\n".join(df['text_repr'].tail(30).tolist()) # Últimos 30
                    
                    answer = get_openai_response(query, context)
                    st.markdown("### Respuesta:")
                    st.write(answer)
                else:
                    st.warning("No hay suficiente información en la base de datos.")
            except Exception as e:
                st.error(f"Error procesando IA: {e}")

def profile_page(db):
    st.header("👤 Mi Perfil")
    user = st.session_state["user_info"]
    
    st.write(f"**Usuario:** {user['user']}")
    st.write(f"**Email:** {user['email']}")
    st.write(f"**Rol:** {user['role']}")
    
    st.divider()
    st.subheader("Cambiar Contraseña")
    
    old_pass = st.text_input("Contraseña Actual", type="password")
    new_pass = st.text_input("Nueva Contraseña", type="password")
    
    if st.button("Actualizar"):
        # Verificar anterior
        if make_hash(old_pass) == user['pass_hash']:
            # Actualizar en Sheets
            try:
                # Buscar la fila 
                cell = db["users"].find(user['user'])
                new_hash = make_hash(new_pass)
                # Asumimos que pass_hash es la columna 2 (índice B)
                db["users"].update_cell(cell.row, 2, new_hash)
                st.success("Contraseña actualizada. Por favor logueate de nuevo.")
                st.session_state["logged_in"] = False
                time.sleep(2)
                st.rerun()
            except Exception as e:
                st.error(f"Error al actualizar: {e}")
        else:
            st.error("La contraseña actual no coincide.")

# -----------------------------------------------------------------------------
# CONTROL DE FLUJO PRINCIPAL
# -----------------------------------------------------------------------------
# --- FUNCIONES DE LAS VISTAS (PANTALLAS) ---

def show_feed(db):
    st.header("🏠 Últimas Novedades")
    try:
        posts_sheet = db["posts"]
        data = posts_sheet.get_all_records()
        
        if not data:
            st.info("Aún no hay publicaciones. ¡Sé el primero!")
            return

        # Convertimos a DataFrame para facilitar el manejo
        df = pd.DataFrame(data)
        
        # Ordenamos para ver el más reciente primero (asumiendo que hay columna 'fecha' o por índice)
        # Si no hay fecha, invertimos el orden simplemente
        df = df.iloc[::-1]

        for index, row in df.iterrows():
            with st.container():
                # Diseño tipo tarjeta con CSS nativo simple
                st.markdown(f"""
                <div style="border:1px solid #e0e0e0; padding:15px; border-radius:10px; margin-bottom:10px; background-color: #1E1E1E;">
                    <div style="display:flex; justify-content:space-between;">
                        <span style="font-weight:bold; color:#FF4B4B;">@{row.get('author', 'Anon')}</span>
                        <span style="font-size:0.8em; color:gray;">{row.get('date', '')}</span>
                    </div>
                    <span style="background-color:#333; padding:2px 8px; border-radius:4px; font-size:0.8em;">{row.get('category', 'General')}</span>
                    <p style="margin-top:10px; font-size:1.1em;">{row.get('content', '')}</p>
                    <p style="color:#2E86C1; font-size:0.9em;">{row.get('hashtags', '')}</p>
                </div>
                """, unsafe_allow_html=True)
                
                # Si hay imagen (url), la mostramos
                if row.get('image_url'):
                    st.image(row['image_url'])
                    
                st.divider()

    except Exception as e:
        st.error(f"Error cargando el feed: {e}")

def create_post_page(db):
    st.header("➕ Nuevo Post")
    
    with st.form("new_post"):
        category = st.selectbox("Categoría", ["Energía", "Transmisión", "Acceso", "Salesforce", "Procesos", "Datos Utiles"])
        content = st.text_area("¿Qué quieres compartir o reportar?")
        
        # Hashtags predefinidos
        tags_list = ["#DatosUtiles", "#PasesAFO", "#Soporte", "#ReclamosEnergia", "#Movistar", "#Claro", "#Salesforce", "#BMC"]
        selected_tags = st.multiselect("Etiquetas", tags_list)
        
        # Por ahora, input de texto para URL de imagen (luego veremos lo de Drive)
        image_url = st.text_input("URL de Imagen (Opcional)")
        
        submitted = st.form_submit_button("Publicar")
        
        if submitted:
            from datetime import datetime
            fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
            author = st.session_state['user']
            tags_str = " ".join(selected_tags)
            
            # Guardar en GSheets
            try:
                posts_sheet = db["posts"]
                # Asegúrate que el orden coincida con tus columnas en Excel:
                # id, date, author, category, content, hashtags, image_url, likes
                posts_sheet.append_row([
                    str(datetime.now().timestamp()), # ID único
                    fecha,
                    author,
                    category,
                    content,
                    tags_str,
                    image_url,
                    0 # Likes iniciales
                ])
                st.success("¡Publicado con éxito!")
                st.balloons()
            except Exception as e:
                st.error(f"Error al publicar: {e}")

def show_explore(db):
    st.header("🔍 Explorar")
    st.info("Funcionalidad de búsqueda avanzada en construcción.")
    # Aquí iría la lógica de filtros

def show_profile(db):
    st.header("👤 Mi Perfil")
    st.write(f"**Usuario:** {st.session_state.get('user')}")
    st.write(f"**Nombre:** {st.session_state.get('name')}")
    st.write(f"**Rol:** {st.session_state.get('role')}")
    
    with st.expander("Cambiar Contraseña"):
        st.warning("Contacta al admin para cambiar tu clave por ahora.")

def show_ai_chat(db):
    st.header("🤖 WikiBot (IA)")
    st.info("Para activar el cerebro de IA, necesitas configurar la API Key de OpenAI.")
    user_q = st.chat_input("Pregúntame algo sobre la operación...")
    if user_q:
        st.write(f"**Tú:** {user_q}")
        st.write("🤖 **Bot:** Lo siento, mi cerebro (API Key) aún no está conectado.")



def main():
    # 1. Configuración inicial de variables de sesión
    if 'authenticated' not in st.session_state:
        st.session_state['authenticated'] = False
    if 'user' not in st.session_state:
        st.session_state['user'] = None

    # 2. Conexión a Base de Datos
    try:
        db = get_db_connection()
    except Exception as e:
        st.error(f"Error crítico conectando a la base de datos: {e}")
        st.stop()

    # 3. CONTROL DE TRÁFICO 
    if not st.session_state['authenticated']:
        # --- MODO NO LOGUEADO ---
        login_page(db)
    else:
        # --- MODO LOGUEADO (DENTRO DE LA APP) ---
        
        
        with st.sidebar:
            
            st.write(f"👤 **{st.session_state['user']}**")
            if st.button("Cerrar Sesión"):
                st.session_state['authenticated'] = False
                st.session_state['user'] = None
                st.rerun()
            
            # Menú de Navegación
            selected = option_menu(
                menu_title="Menú Principal",
                options=["Home", "Explorar", "Nuevo Post", "Perfil", "Chat IA"],
                icons=["house", "search", "plus-circle", "person", "robot"],
                menu_icon="cast",
                default_index=0,
            )

        # Lógica de navegación
        if selected == "Home":
            show_feed(db) 
        elif selected == "Explorar":
            show_explore(db)
        elif selected == "Nuevo Post":
            create_post_page(db)
        elif selected == "Perfil":
            show_profile(db)
        elif selected == "Chat IA":
            show_ai_chat(db)

# Ejecución del script
if __name__ == "__main__":
    main()