import os
import re
import json
import pandas as pd
import pdfplumber
from pdf2image import convert_from_path
import pytesseract
from typing import List, Dict, Tuple, Optional
from datetime import datetime
import uuid
import openai
from app.config import settings

# Configuración de OpenAI (la API key se lee desde variables de entorno)
openai.api_key = settings.OPENAI_API_KEY

# ------------------------------------------------------------
# 1. EXTRACCIÓN DE TEXTO DESDE PDF (nativo u OCR)
# ------------------------------------------------------------
def extract_text_from_pdf(pdf_path: str, use_ocr: bool = True) -> str:
    """
    Extrae texto de un PDF.
    Si use_ocr=True y el PDF no tiene texto, usa OCR con Tesseract.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if text.strip():
                return text
    except:
        pass

    if use_ocr:
        images = convert_from_path(pdf_path, dpi=300)
        ocr_text = ""
        for img in images:
            ocr_text += pytesseract.image_to_string(img, lang='spa+eng') + "\n"
        return ocr_text
    return ""

# ------------------------------------------------------------
# 2. LIMPIEZA Y ESTANDARIZACIÓN DE CAMPOS
# ------------------------------------------------------------
def clean_name(name: str) -> str:
    name = re.sub(r'\s+', ' ', name).strip()
    return name.title()

def clean_phone(phone: str) -> str:
    digits = re.sub(r'[^\d+]', '', phone)
    if digits.startswith('+'):
        return digits
    elif len(digits) == 9:
        return f"+34{digits}"
    else:
        return digits

def clean_date(date_str: str) -> str:
    patterns = [
        (r'(\d{1,2})/(\d{1,2})/(\d{4})', lambda m: f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"),
        (r'(\d{4})-(\d{1,2})-(\d{1,2})', lambda m: f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"),
        (r'(\d{1,2})-(\d{1,2})-(\d{4})', lambda m: f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}")
    ]
    for pattern, repl in patterns:
        m = re.search(pattern, date_str)
        if m:
            return repl(m)
    return date_str

def clean_email(email: str) -> str:
    email = email.strip().lower()
    email = re.sub(r'\s', '', email)
    return email

def clean_address(address: str) -> str:
    address = re.sub(r'\s+', ' ', address).strip()
    return address.title()

# ------------------------------------------------------------
# 3. EXTRACCIÓN CON EXPRESIONES REGULARES (FALLBACK)
# ------------------------------------------------------------
def extract_fields_with_regex(text: str) -> Dict[str, str]:
    """Extrae campos usando expresiones regulares (método tradicional)."""
    patterns = {
        'nombre': r'(?:Nombre|Cliente|Name|Full name)[:\s]*([A-Za-záéíóúñÁÉÍÓÚÑ\s]+)(?:\n|$)',
        'direccion': r'(?:Dirección|Address|Dir)[:\s]*([^\n]+)',
        'telefono': r'(?:Teléfono|Phone|Tel|Móvil|Mobile)[:\s]*([+\d\s\-\(\)]+)',
        'email': r'(?:Email|Correo|E-mail)[:\s]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        'cuenta': r'(?:Cuenta|Account|Nº cuenta|Account number)[:\s]*([A-Z0-9]+)',
        'fecha': r'(?:Fecha|Date|F\.Nacimiento|Birth)[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})'
    }
    extracted = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        extracted[field] = match.group(1).strip() if match else ""

    # Búsqueda genérica si fallan los patrones
    if not extracted['telefono']:
        phone_match = re.search(r'[\+\d]{8,15}', text)
        if phone_match:
            extracted['telefono'] = phone_match.group()
    if not extracted['email']:
        email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        if email_match:
            extracted['email'] = email_match.group()
    return extracted

# ------------------------------------------------------------
# 4. EXTRACCIÓN CON IA (STRUCTURED OUTPUTS)
# ------------------------------------------------------------
def extract_fields_with_ai(text_block: str) -> Dict[str, str]:
    """
    Extrae campos usando OpenAI con response_format={'type': 'json_object'}.
    Si falla, retorna diccionario vacío para que se use el fallback.
    """
    prompt = f"""
Eres un asistente experto en extraer información de clientes desde documentos.
Del siguiente texto, extrae los siguientes campos: nombre, dirección, teléfono, email, número de cuenta, fecha.
Devuelve ÚNICAMENTE un objeto JSON con estas claves exactas: "nombre", "direccion", "telefono", "email", "cuenta", "fecha".
Si un campo no aparece en el texto, pon una cadena vacía.
No incluyas explicaciones, solo el JSON.

Texto:
{text_block[:2500]}
"""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo-1106",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            timeout=10
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        # Normalizar claves
        return {
            "nombre": data.get("nombre", ""),
            "direccion": data.get("direccion", ""),
            "telefono": data.get("telefono", ""),
            "email": data.get("email", ""),
            "cuenta": data.get("cuenta", ""),
            "fecha": data.get("fecha", "")
        }
    except Exception as e:
        print(f"Error en extracción con IA: {e}")
        return {}

# ------------------------------------------------------------
# 5. PROCESAMIENTO DE UN PDF (usa IA si está disponible, sino regex)
# ------------------------------------------------------------
def process_pdf(pdf_path: str, use_ai: bool = True) -> List[Dict[str, str]]:
    """
    Procesa un PDF y devuelve una lista de registros (uno por bloque de cliente).
    Si use_ai=True, intenta primero con IA; si falla o no hay campos, usa regex.
    """
    full_text = extract_text_from_pdf(pdf_path)
    # Dividir por dobles saltos de línea (bloques)
    blocks = re.split(r'\n\s*\n', full_text)
    records = []
    for block in blocks:
        # Solo procesar bloques que parezcan contener datos de cliente
        if not re.search(r'(Nombre|Cliente|Name|Dirección|Address|Teléfono|Phone)', block, re.IGNORECASE):
            continue
        if use_ai:
            fields = extract_fields_with_ai(block)
            # Si la IA devuelve todos vacíos, fallamos a regex
            if not any(fields.values()):
                fields = extract_fields_with_regex(block)
        else:
            fields = extract_fields_with_regex(block)

        if fields.get('nombre') or fields.get('telefono') or fields.get('email'):
            records.append(fields)
    return records

# ------------------------------------------------------------
# 6. ESTANDARIZACIÓN Y VALIDACIÓN DEL DATAFRAME
# ------------------------------------------------------------
def standardize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if 'nombre' in df.columns:
        df['nombre'] = df['nombre'].astype(str).apply(clean_name)
    if 'telefono' in df.columns:
        df['telefono'] = df['telefono'].astype(str).apply(clean_phone)
    if 'email' in df.columns:
        df['email'] = df['email'].astype(str).apply(clean_email)
    if 'direccion' in df.columns:
        df['direccion'] = df['direccion'].astype(str).apply(clean_address)
    if 'fecha' in df.columns:
        df['fecha'] = df['fecha'].astype(str).apply(clean_date)
    if 'cuenta' in df.columns:
        df['cuenta'] = df['cuenta'].astype(str).apply(lambda x: x.strip().upper())

    # Eliminar espacios sobrantes en todas las celdas string
    df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
    return df

def validate_dataframe(df: pd.DataFrame, mandatory_fields: List[str]) -> Tuple[pd.DataFrame, Dict]:
    """
    Detecta duplicados, campos obligatorios vacíos y genera log.
    """
    log = {
        'duplicados': [],
        'campos_faltantes': [],
        'checksums': {}
    }

    # Duplicados basados en nombre+teléfono si existen
    if 'nombre' in df and 'telefono' in df:
        df['duplicado_hash'] = df['nombre'].str.lower() + df['telefono'].str.replace(r'\D', '', regex=True)
        dups = df[df.duplicated('duplicado_hash', keep=False)]
        log['duplicados'] = dups.index.tolist()
        df['es_duplicado'] = df.duplicated('duplicado_hash', keep=False)
    else:
        df['es_duplicado'] = False

    # Validar campos obligatorios
    for field in mandatory_fields:
        if field not in df.columns:
            df[field] = ""
        missing = df[df[field].isna() | (df[field] == "")]
        log['campos_faltantes'].append({field: missing.index.tolist()})
        df[f'falta_{field}'] = df[field].isna() | (df[field] == "")

    log['checksums']['total_registros'] = len(df)
    log['checksums']['duplicados_encontrados'] = sum(df['es_duplicado'])

    return df, log

# ------------------------------------------------------------
# 7. FUNCIÓN PRINCIPAL (PROCESAMIENTO DE MÚLTIPLES PDFs)
# ------------------------------------------------------------
def process_pdfs_to_excel(pdf_paths: List[str], user_id: int = None, use_ai: bool = True) -> Tuple[str, str]:
    """
    Convierte una lista de rutas de PDFs en un archivo Excel y un log.
    Retorna (ruta_excel, ruta_log).
    """
    all_records = []
    for path in pdf_paths:
        records = process_pdf(path, use_ai=use_ai)
        for rec in records:
            rec['archivo_origen'] = os.path.basename(path)
        all_records.extend(records)

    if not all_records:
        raise ValueError("No se extrajo ningún registro de los PDFs.")

    df = pd.DataFrame(all_records)
    df = standardize_dataframe(df)

    mandatory = ["nombre", "telefono"]  # configurables
    df, log = validate_dataframe(df, mandatory)

    # Generar nombres únicos para los archivos de salida
    task_id = str(uuid.uuid4())
    excel_filename = f"extracted_{task_id}.xlsx"
    log_filename = f"log_{task_id}.txt"

    excel_path = os.path.join(settings.OUTPUT_DIR, excel_filename)
    log_path = os.path.join(settings.OUTPUT_DIR, log_filename)

    # Asegurar que exista el directorio de salida
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)

    df.to_excel(excel_path, index=False)

    with open(log_path, 'w', encoding='utf-8') as f:
        f.write("=== LOG DE EXTRACCIÓN Y VALIDACIÓN ===\n\n")
        f.write(f"Total registros extraídos: {log['checksums']['total_registros']}\n")
        f.write(f"Duplicados encontrados: {log['checksums']['duplicados_encontrados']}\n")
        f.write(f"Filas duplicadas (índices): {log['duplicados']}\n\n")
        f.write("Campos obligatorios faltantes:\n")
        for item in log['campos_faltantes']:
            f.write(str(item) + "\n")
        f.write("\nNota: Revisar columnas 'es_duplicado' y 'falta_*' en el Excel para más detalle.\n")

    return excel_path, log_path